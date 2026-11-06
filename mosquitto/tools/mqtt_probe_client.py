"""Small synchronous MQTT 3.1.1/5 client for controlled ACL probes."""

import base64
import os
import socket
import ssl
import struct
import uuid

DOMAIN = "iot.panozoelectronica.com.ar"

def vint(number):
    result = bytearray()
    while True:
        digit = number % 128
        number //= 128
        result.append(digit | (128 if number else 0))
        if not number:
            return bytes(result)


def utf(text):
    raw = text.encode()
    return struct.pack("!H", len(raw)) + raw


class MQTT:
    def __init__(self, port, username=None, password=None, secure=False, websocket=False,
                 hostname=DOMAIN, trusted=True, version=None, mqtt_level=5, client_id=None,
                 connect_host="127.0.0.1", cafile="/fixtures/ca.pem"):
        self.sock = socket.create_connection((connect_host, port), timeout=3)
        self.ws = websocket
        self.level = mqtt_level
        self.buffer = b""
        self.pending = []
        try:
            if secure:
                context = ssl.create_default_context(cafile=cafile if trusted else None)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                if version:
                    context.minimum_version = context.maximum_version = version
                    if version < ssl.TLSVersion.TLSv1_2:
                        context.set_ciphers("DEFAULT:@SECLEVEL=0")
                self.sock = context.wrap_socket(self.sock, server_hostname=hostname)
            if websocket:
                key = base64.b64encode(os.urandom(16)).decode()
                request = ("GET /mqtt HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                           "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                           "Sec-WebSocket-Protocol: mqtt\r\nSec-WebSocket-Key: " + key + "\r\n\r\n")
                self.sock.sendall(request.encode())
                header = b""
                while not header.endswith(b"\r\n\r\n"):
                    header += self.exact(1)
                if b" 101 " not in header.split(b"\r\n", 1)[0]:
                    raise RuntimeError("WebSocket upgrade failed")
            flags = 2 | (0xC0 if username is not None else 0)
            body = utf("MQTT") + bytes([mqtt_level, flags, 0, 30])
            body += (b"\x00" if mqtt_level == 5 else b"") + utf(client_id or "tls-test-" + uuid.uuid4().hex)
            if username is not None:
                body += utf(username) + utf(password)
            self.send(0x10, body)
            kind, body = self.packet()
            if kind != 0x20:
                raise RuntimeError("Expected CONNACK")
            self.reason = body[1]
        except Exception:
            self.sock.close()
            raise

    def exact(self, length):
        value = bytearray()
        while len(value) < length:
            chunk = self.sock.recv(length - len(value))
            if not chunk:
                raise ConnectionError("Peer closed connection")
            value.extend(chunk)
        return bytes(value)

    def read(self, length):
        if not self.ws:
            return self.exact(length)
        while len(self.buffer) < length:
            head = self.exact(2)
            opcode, count = head[0] & 15, head[1] & 127
            if count == 126:
                count = struct.unpack("!H", self.exact(2))[0]
            elif count == 127:
                count = struct.unpack("!Q", self.exact(8))[0]
            frame = self.exact(count)
            if opcode == 8:
                raise ConnectionError("WebSocket closed")
            if opcode in (0, 2):
                self.buffer += frame
        value, self.buffer = self.buffer[:length], self.buffer[length:]
        return value

    def send(self, header, body=b""):
        value = bytes([header]) + vint(len(body)) + body
        if self.ws:
            mask = os.urandom(4)
            size = len(value)
            head = bytes([0x82, 0x80 | size]) if size < 126 else bytes([0x82, 0xFE]) + struct.pack("!H", size)
            value = head + mask + bytes(x ^ mask[i % 4] for i, x in enumerate(value))
        self.sock.sendall(value)

    def packet(self):
        header = self.read(1)[0]
        length, shift = 0, 0
        while True:
            digit = self.read(1)[0]
            length += (digit & 127) << shift
            if not digit & 128:
                break
            shift += 7
        return header, self.read(length)

    def subscribe(self, topic):
        self.send(0x82, b"\x00\x01" + (b"\x00" if self.level == 5 else b"") + utf(topic) + b"\x01")
        kind, data = self.packet()
        while kind & 0xF0 == 0x30:
            self.pending.append((kind, data))
            kind, data = self.packet()
        if kind != 0x90:
            raise RuntimeError("Expected SUBACK")
        return data[-1]

    def publish(self, topic, payload=b"isolated-tls-fixture"):
        self.send(0x32, utf(topic) + b"\x00\x02" + (b"\x00" if self.level == 5 else b"") + payload)
        kind, data = self.packet()
        while kind & 0xF0 == 0x30:
            self.pending.append((kind, data))
            kind, data = self.packet()
        if kind != 0x40:
            raise RuntimeError("Expected PUBACK")
        return data[2] if len(data) > 2 else 0

    def receive(self):
        self.sock.settimeout(0.25)
        try:
            kind, data = self.pending.pop(0) if self.pending else self.packet()
            if kind & 0xF0 != 0x30:
                raise RuntimeError("Expected PUBLISH")
            length = struct.unpack("!H", data[:2])[0]
            topic = data[2:2 + length].decode()
            offset = 2 + length
            if (kind >> 1) & 3:
                packet_id = data[offset:offset + 2]
                offset += 2
                self.send(0x40, packet_id)
            properties, shift = 0, 0
            while self.level == 5:
                digit = data[offset]
                offset += 1
                properties += (digit & 127) << shift
                if not digit & 128:
                    break
                shift += 7
            return topic, data[offset + properties:]
        except socket.timeout:
            return None
        finally:
            self.sock.settimeout(3)

    def close(self):
        self.sock.close()


