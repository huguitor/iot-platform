"""Offline-listener tests: no published host ports and no active MQTT connections."""

from pathlib import Path
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def checked(args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        # Command output may contain credential-related diagnostics. Do not echo it.
        raise RuntimeError("Isolated setup command failed: " + args[0])
    return result


def active_state():
    ids = checked(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=iot-platform"], text=True).stdout.split()
    containers = json.loads(checked(["docker", "inspect", *ids]).stdout)
    if any("mqtt-worker" in c["Name"] for c in containers):
        raise RuntimeError("Worker container found; stop and investigate")
    return {c["Name"]: (c["Id"], c["State"]["StartedAt"], c["RestartCount"], c["State"]["Running"]) for c in containers}


def main():
    os.umask(0o077)
    baseline = active_state()
    broker = json.loads(checked(["docker", "inspect", "iot-platform-mosquitto"]).stdout)[0]
    api = json.loads(checked(["docker", "inspect", "iot-platform-api"]).stdout)[0]
    env = dict(x.split("=", 1) for x in api["Config"]["Env"] if "=" in x)
    candidate = json.loads(checked(["docker", "compose", "-f", "docker-compose.yml", "-f",
                                  "docker-compose.mqtt-tls.yml", "config", "--format", "json"], cwd=ROOT).stdout)
    if candidate["services"]["mosquitto"]["image"].split("@")[-1] != broker["Image"]:
        raise RuntimeError("Active image differs from the candidate pin; re-evaluate test image")
    suffix = uuid.uuid4().hex[:12]
    name = "iot-tls-test-" + suffix
    runner_name = name + "-client"
    created = False
    with tempfile.TemporaryDirectory(prefix="iot-tls-fixtures-") as folder:
        fixtures = Path(folder)
        # Copy only a private, ephemeral hash file and ACL, never the active data.
        passwords = checked(["docker", "exec", "iot-platform-mosquitto", "cat", "/mosquitto/config/passwordfile"]).stdout
        acl = checked(["docker", "exec", "iot-platform-mosquitto", "cat", "/mosquitto/config/acl"]).stdout
        (fixtures / "passwordfile").write_bytes(passwords)
        (fixtures / "acl").write_bytes(acl)
        public_acl = checked(["docker", "exec", "iot-platform-mosquitto", "cat", "/mosquitto/config/acl-public"]).stdout
        (fixtures / "acl-public").write_bytes(public_acl)
        password = secrets.token_urlsafe(32)
        # -U converts this private plaintext fixture in place, with no secret argv.
        (fixtures / "public.passwordfile").write_text("lab-esp8266-dell-01:" + password + "\n")
        checked(["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                 "--mount", f"type=bind,src={fixtures},dst=/fixtures",
                 "--entrypoint", "mosquitto_passwd", broker["Image"], "-U", "/fixtures/public.passwordfile"])
        internal_users = {line.split(":", 1)[0] for line in passwords.decode().splitlines() if ":" in line}
        public_users = {line.split(":", 1)[0] for line in (fixtures / "public.passwordfile").read_text().splitlines() if ":" in line}
        if public_users != {"lab-esp8266-dell-01"} or internal_users & public_users:
            raise RuntimeError("Credential files are not disjoint; STOP")
        checked(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                 "-nodes", "-keyout", str(fixtures / "ca.key"), "-out", str(fixtures / "ca.pem"),
                 "-days", "2", "-subj", "/CN=Disposable IoT Lab CA"])
        checked(["openssl", "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                 "-nodes", "-keyout", str(fixtures / "server.key"), "-out", str(fixtures / "server.csr"),
                 "-subj", "/CN=iot.panozoelectronica.com.ar"])
        (fixtures / "extensions.cnf").write_text("subjectAltName=DNS:iot.panozoelectronica.com.ar\nextendedKeyUsage=serverAuth\n")
        checked(["openssl", "x509", "-req", "-in", str(fixtures / "server.csr"), "-CA", str(fixtures / "ca.pem"),
                 "-CAkey", str(fixtures / "ca.key"), "-CAcreateserial", "-out", str(fixtures / "server.pem"),
                 "-days", "1", "-extfile", str(fixtures / "extensions.cnf")])
        configuration = (ROOT / "mosquitto/config/mosquitto-tls.conf").read_text()
        configuration = configuration.replace("/mosquitto/config/", "/fixtures/")
        configuration = configuration.replace("/mosquitto/public-auth/passwordfile", "/fixtures/public.passwordfile")
        configuration = configuration.replace("/mosquitto/tls/current/fullchain.pem", "/fixtures/server.pem")
        configuration = configuration.replace("/mosquitto/tls/current/privkey.pem", "/fixtures/server.key")
        (fixtures / "mosquitto.conf").write_text(configuration)
        for path in fixtures.iterdir():
            path.chmod(0o600)
        args = ["docker", "run", "--name", name, "--network", "none", "--read-only",
                "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "128m", "--cpus", "1", "--pids-limit", "64",
                "--tmpfs", "/mosquitto/data:rw,mode=1777,size=16m",
                "--mount", f"type=bind,src={fixtures},dst=/fixtures,readonly",
                "--entrypoint", "mosquitto", broker["Image"], "-c", "/fixtures/mosquitto.conf"]
        # Syntax check in a separate ephemeral container, then actual startup.
        checked(args[:2] + ["--rm"] + args[2:] + ["--test-config"])
        try:
            checked(args[:2] + ["--detach"] + args[2:], text=True)
            created = True
            for _ in range(30):
                ready = subprocess.run(["docker", "exec", name, "nc", "-z", "127.0.0.1", "8883"], capture_output=True)
                if ready.returncode == 0:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Disposable TLS broker did not become ready")
            payload = json.dumps({"internal_username": env["MQTT_USERNAME"],
                                  "internal_password": env["MQTT_PASSWORD"], "public_password": password})
            client_args = ["docker", "run", "--rm", "-i", "--name", runner_name, "--network", "container:" + name,
                 "--read-only", "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "128m", "--cpus", "1",
                 "--mount", f"type=bind,src={fixtures},dst=/fixtures,readonly",
                 "--mount", f"type=bind,src={ROOT / 'mosquitto/tests'},dst=/tests,readonly",
                 "--mount", f"type=bind,src={ROOT / 'mosquitto/tools'},dst=/tools,readonly",
                 "-e", "IOT_TLS_DISPOSABLE=1", "--entrypoint", "python", api["Image"], "-B", "/tests/test_tls.py"]
            result = subprocess.run(client_args, input=payload, text=True, capture_output=True)
            # Tests print only test names, reason-code assertions and synthetic payloads.
            print(result.stdout, end="")
            print(result.stderr, end="")
            if result.returncode:
                raise RuntimeError("Credential/listener separation NOT verified; STOP, no deployment")
            print("PASS: active internal credentials rejected on TLS; public credentials rejected on both internal listeners")
            # Exercise renewal using only the disposable broker and temporary key.
            initial_cert = (fixtures / "server.pem").read_bytes()
            checked(["openssl", "x509", "-req", "-in", str(fixtures / "server.csr"), "-CA", str(fixtures / "ca.pem"),
                     "-CAkey", str(fixtures / "ca.key"), "-set_serial", "0x" + uuid.uuid4().hex,
                     "-out", str(fixtures / "server.pem"), "-days", "2", "-extfile", str(fixtures / "extensions.cnf")])
            if (fixtures / "server.pem").read_bytes() == initial_cert:
                raise RuntimeError("Renewal fixture did not change")
            checked(["docker", "kill", "--signal", "HUP", name])
            expected = hashlib.sha256(checked(["openssl", "x509", "-in", str(fixtures / "server.pem"), "-outform", "DER"]).stdout).hexdigest()
            probe = ("import ssl,socket,hashlib; c=ssl.create_default_context(cafile='/fixtures/ca.pem'); "
                     "s=c.wrap_socket(socket.create_connection(('127.0.0.1',8883)),server_hostname='iot.panozoelectronica.com.ar'); "
                     "print(hashlib.sha256(s.getpeercert(binary_form=True)).hexdigest()); s.close()")
            for _ in range(20):
                observed = checked(["docker", "run", "--rm", "--network", "container:" + name, "--read-only",
                                    "--mount", f"type=bind,src={fixtures},dst=/fixtures,readonly",
                                    "--entrypoint", "python", api["Image"], "-B", "-c", probe], text=True).stdout.strip()
                if observed == expected:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Disposable broker did not reload the renewed certificate")
            print("PASS: directed SIGHUP loaded a renewed certificate on the disposable broker")
            # Certificate renewal must not alter the security boundary.
            renewed = subprocess.run(client_args, input=payload, text=True, capture_output=True)
            print(renewed.stdout, end="")
            print(renewed.stderr, end="")
            if renewed.returncode:
                raise RuntimeError("Listener isolation after certificate reload NOT verified; STOP")
            print("PASS: all listener/authentication/ACL tests also passed after certificate reload")
            # Authorize the synthetic namespace only in the disposable ACL copy.
            with (fixtures / "acl-public").open("a") as f:
                f.write("\ntopic write iot/devices/acl-probe/+/write-ok\n"
                        "topic read iot/devices/acl-probe/+/read-ok\n")
            (fixtures / "acl-probe-credentials.json").write_text(json.dumps({
                "device": {"username": "lab-esp8266-dell-01", "password": password},
                "observer": {"username": env["MQTT_USERNAME"], "password": env["MQTT_PASSWORD"]},
            }))
            (fixtures / "acl-probe-credentials.json").chmod(0o600)
            checked(["docker", "kill", "--signal", "HUP", name])
            # Invoke the same production probe, protected by fixture network checks.
            probe_args = client_args[:-2] + ["/tools/check-mqtt-acl.py", "--run", "--test-fixture",
                         "--credentials", "/fixtures/acl-probe-credentials.json", "--ca-file", "/fixtures/ca.pem"]
            # client_args ends in '-B', '/tests/test_tls.py': preserve -B explicitly.
            probe_args.insert(probe_args.index("/tools/check-mqtt-acl.py"), "-B")
            synthetic = subprocess.run(probe_args, text=True, capture_output=True)
            print(synthetic.stdout, end="")
            if synthetic.returncode:
                raise RuntimeError("Synthetic ACL read/write probe failed; no production changes attempted")
            print("PASS: standalone synthetic ACL probe validated read/write allow and deny with delivery barriers")
        finally:
            if created:
                checked(["docker", "rm", "-f", "-v", name])
            left = subprocess.run(["docker", "inspect", runner_name], capture_output=True)
            if left.returncode == 0:
                checked(["docker", "rm", "-f", runner_name])
    if active_state() != baseline:
        raise RuntimeError("Active services changed during the test; STOP")
    print("PASS: active service IDs/start times/restarts unchanged; worker absent; disposable fixtures removed")


if __name__ == "__main__":
    main()
