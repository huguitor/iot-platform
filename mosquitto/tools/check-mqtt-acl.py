"""MQTT 5 ACL checks with synthetic topics only. Default is a side-effect-free plan."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

from mqtt_probe_client import MQTT, DOMAIN

SECRET_ROOT = Path("/srv/iot-platform-secrets/mqtt")


def load_credentials(path, fixture=False):
    path = Path(path).resolve()
    if not fixture and not path.is_relative_to(SECRET_ROOT):
        raise ValueError("Credential file must be inside the private MQTT directory")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Credential file must be a regular private file, mode 0600 or stricter")
    if info.st_uid not in (0, os.geteuid()) or info.st_size > 16384:
        raise ValueError("Credential file owner or size is invalid")
    data = json.loads(path.read_text())
    for role in ("device", "observer"):
        if not all(isinstance(data[role].get(k), str) and data[role][k] for k in ("username", "password")):
            raise ValueError("Credential profile is incomplete")
    if data["device"]["username"] == data["observer"]["username"]:
        raise ValueError("Device and observer must have distinct identities")
    return {role: {key: data[role][key] for key in ("username", "password")}
            for role in ("device", "observer")}


def verify(credentials, namespace, fixture=False, ca_file=None):
    if not re.fullmatch(r"iot/devices/acl-probe/[a-f0-9]{12,32}", namespace):
        raise ValueError("Only the dedicated fictitious acl-probe namespace is permitted")
    clients = []
    try:
        device = MQTT(8883, **credentials["device"], secure=True,
                      connect_host="127.0.0.1" if fixture else DOMAIN, cafile=ca_file)
        clients.append(device)
        observer = MQTT(1883, **credentials["observer"])
        clients.append(observer)
        if device.reason != 0 or observer.reason != 0:
            raise RuntimeError("Authentication failed; no PUBLISH sent")
        write_ok, write_no = namespace + "/write-ok", namespace + "/write-denied"
        read_ok, read_no = namespace + "/read-ok", namespace + "/read-denied"
        if observer.subscribe(namespace + "/#") >= 128:
            raise RuntimeError("Observer subscription rejected")
        marker = uuid.uuid4().hex.encode()
        if device.publish(write_ok, marker) != 0:
            raise RuntimeError("Allowed synthetic write was rejected; stop and review authorized test ACL")
        if observer.receive() != (write_ok, marker):
            raise RuntimeError("Allowed write was not delivered")
        if device.subscribe(read_ok) >= 128:
            raise RuntimeError("Allowed synthetic read subscription rejected")
        if observer.publish(read_ok, marker) != 0:
            raise RuntimeError("Observer publish failed")
        # Observer is also subscribed; consume its own synthetic read message.
        if observer.receive() != (read_ok, marker) or device.receive() != (read_ok, marker):
            raise RuntimeError("Allowed read was not delivered")
        denied_suback = device.subscribe(read_no)
        if observer.publish(read_no, marker) != 0:
            raise RuntimeError("Read-negative stimulus was not accepted")
        if observer.receive() != (read_no, marker):
            raise RuntimeError("Read-negative observer control failed")
        # A permitted barrier proves delivery is alive; do not count a timeout or
        # successful SUBACK as evidence that the forbidden read was denied.
        barrier = b"barrier-" + marker
        if observer.publish(read_ok, barrier) != 0:
            raise RuntimeError("Read-negative barrier publish failed")
        if observer.receive() != (read_ok, barrier) or device.receive() != (read_ok, barrier):
            raise RuntimeError("Forbidden read leaked or delivery barrier failed")
        if device.receive() is not None:
            raise RuntimeError("Unexpected delivery after read-negative barrier")
        denied_write_rc = device.publish(write_no, marker)
        if denied_write_rc != 135:
            raise RuntimeError("Forbidden write did not return MQTT 5 PUBACK RC 135")
        if device.publish(write_ok, barrier) != 0 or observer.receive() != (write_ok, barrier):
            raise RuntimeError("Forbidden write leaked or observer barrier failed")
        if observer.receive() is not None:
            raise RuntimeError("Unexpected delivery after write-negative barrier")
        return {"result": "PASS", "mqtt": 5, "retain": False,
                "write_allowed": {"puback": 0, "delivered": True},
                "write_denied": {"puback": denied_write_rc, "not_delivered": True},
                "read_allowed": {"delivered": True},
                "read_denied": {"suback": denied_suback, "not_delivered_before_barrier": True}}
    finally:
        for client in clients:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="send synthetic test messages; requires an authorized test namespace")
    parser.add_argument("--credentials", type=Path, default=SECRET_ROOT / "acl-probe-credentials.json")
    parser.add_argument("--namespace", default="iot/devices/acl-probe/" + uuid.uuid4().hex[:16])
    parser.add_argument("--test-fixture", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ca-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"mode": "plan", "no_connections_or_credential_reads": True,
                          "credentials": str(args.credentials), "namespace": args.namespace,
                          "requires": "authorized synthetic ACL and distinct observer credentials"}))
        return
    try:
        if args.test_fixture:
            routes = Path("/proc/net/route").read_text().splitlines()[1:]
            if os.getenv("IOT_TLS_DISPOSABLE") != "1" or any(l.split()[0] != "lo" for l in routes):
                raise RuntimeError("Fixture mode requires an isolated network namespace")
        else:
            if args.ca_file:
                raise ValueError("Production connections use the system trusted CA store")
            result = subprocess.run(["docker", "ps", "-q", "--filter", "name=iot-platform-mqtt-worker"],
                                    capture_output=True, check=True)
            if result.stdout.strip():
                raise RuntimeError("Worker is running; stop before probes")
        credentials = load_credentials(args.credentials, fixture=args.test_fixture)
        print(json.dumps(verify(credentials, args.namespace, fixture=args.test_fixture, ca_file=args.ca_file)))
    except Exception as exc:
        # Never print exception text, credentials, broker packets, payloads or traceback.
        print(json.dumps({"result": "FAIL", "error_type": type(exc).__name__,
                          "action": "stop; review permissions, credentials and authorized test ACL"}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
