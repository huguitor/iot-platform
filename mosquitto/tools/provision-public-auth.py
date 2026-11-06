"""Future root-only provisioning, outside Git; never changes active credentials."""

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess

BASE = Path("/srv/iot-platform-secrets/mqtt")
USERNAME = "lab-esp8266-dell-01"
DEVICE = "lab-esp8266-dell-20261104-01"
IMAGE = "eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408"


def hash_passwordfile(auth, user=None):
    """mosquitto_passwd needs a writable temporary directory even with a bind mount."""
    args = ["docker", "run", "--rm", "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,mode=1777,size=8m"]
    if user is not None:
        args += ["--user", user]
    args += ["--mount", f"type=bind,src={auth},dst=/auth",
             "--entrypoint", "mosquitto_passwd", IMAGE, "-U", "/auth/passwordfile"]
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        raise SystemExit("Hash generation failed; STOP. Private files retained for review")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Provision a NEW public MQTT credential outside Git; never rotate existing accounts")
    parser.parse_args(argv)  # --help exits before privilege checks, Docker or filesystem writes.
    if os.geteuid() != 0:
        raise SystemExit("Run as root only during explicitly authorized provisioning")
    os.umask(0o077)
    auth = BASE / "auth"
    passwordfile = auth / "passwordfile"
    private_record = BASE / "lab-device-credential.json"
    if passwordfile.exists() or private_record.exists():
        raise SystemExit("Refusing to overwrite an existing laboratory credential")
    # Verify prerequisites before writing any secret; never pull implicitly.
    subprocess.run(["docker", "image", "inspect", IMAGE], check=True, stdout=subprocess.DEVNULL)
    internal = subprocess.run(
        ["docker", "exec", "iot-platform-mosquitto", "cat", "/mosquitto/config/passwordfile"],
        capture_output=True,
    )
    if internal.returncode:
        raise SystemExit("Cannot verify internal account names; STOP before provisioning")
    names = {line.split(b":", 1)[0] for line in internal.stdout.splitlines() if b":" in line}
    if USERNAME.encode() in names:
        raise SystemExit("Laboratory username already exists internally; STOP")
    BASE.mkdir(parents=True, exist_ok=True)
    auth.mkdir(mode=0o750, exist_ok=True)
    os.chown(auth, 0, 1883)
    auth.chmod(0o750)
    password = secrets.token_urlsafe(32)
    # Recovery information is root-only and outside the mounted auth directory.
    with private_record.open("x") as f:
        json.dump({"username": USERNAME, "device_id": DEVICE, "password": password}, f)
    with passwordfile.open("x") as f:
        f.write(USERNAME + ":" + password + "\n")
    hash_passwordfile(auth)
    os.chown(passwordfile, 0, 1883)
    passwordfile.chmod(0o640)
    if ":$" not in passwordfile.read_text():
        raise SystemExit("Password file format validation failed; STOP")
    print("Laboratory identity provisioned outside Git; no active broker credentials changed")
    print("Private credential record:", private_record)


if __name__ == "__main__":
    main()
