"""Stage a verified Certbot bundle atomically; reload only a deployed TLS broker."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import ssl
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

DOMAIN = "iot.panozoelectronica.com.ar"
TLS_DIR = Path("/srv/iot-platform-secrets/mqtt/tls")


class RenewalError(RuntimeError):
    """Operational error with a fixed, non-sensitive diagnostic."""


@contextmanager
def exclusive_lock():
    TLS_DIR.mkdir(parents=True, exist_ok=True, mode=0o750)
    path = TLS_DIR / ".renew.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RenewalError("Unsafe TLS renewal lock file; nothing staged or signalled")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RenewalError("Another TLS renewal is running; nothing staged or signalled") from None
        yield
    finally:
        # Closing releases the lock, including on failure. Never unlink the inode:
        # another process could otherwise lock a different replacement inode.
        os.close(descriptor)


def certificate_fingerprint(cert):
    leaf = subprocess.run(["openssl", "x509", "-in", str(cert), "-outform", "DER"],
                          capture_output=True, check=True, timeout=10).stdout
    return hashlib.sha256(leaf).hexdigest()


def verify_served_certificate(endpoint, expected_fingerprint, timeout=20, context=None):
    """New TLS connections: verify trust, hostname and exact leaf fingerprint."""
    if not 0 < timeout <= 120:
        raise ValueError("Invalid TLS verification timeout")
    context = context or ssl.create_default_context()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RenewalError("Timed out verifying the expected MQTT TLS certificate")
        try:
            with socket.create_connection(endpoint, timeout=min(3, remaining)) as raw:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                raw.settimeout(min(3, remaining))
                with context.wrap_socket(raw, server_hostname=DOMAIN) as secure:
                    actual = hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest()
                    if actual == expected_fingerprint:
                        return
        except (OSError, ssl.SSLError):
            pass  # Retry only inside the bounded window; do not log certificate data.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.2, remaining))
    raise RenewalError("Timed out verifying the expected MQTT TLS certificate")


def atomic_current(target):
    temporary = TLS_DIR / (".current-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        temporary.replace(TLS_DIR / "current")
    finally:
        temporary.unlink(missing_ok=True)


def previous_current(required=False):
    current = TLS_DIR / "current"
    if not current.is_symlink():
        if current.exists() or required:
            raise RenewalError("A valid previous current symlink is required; no certificate changed")
        return None
    target = os.readlink(current)
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to((TLS_DIR / "releases").resolve()) or not resolved.is_dir():
        raise RenewalError("Previous current target is outside the managed releases directory")
    return target


def broker_tls_endpoint(broker_id):
    result = subprocess.run(["docker", "inspect", broker_id], capture_output=True, check=True, timeout=10)
    broker = json.loads(result.stdout)[0]
    bindings = broker["HostConfig"]["PortBindings"]["8883/tcp"]
    # Numeric local binding avoids public DNS/CDN checks hitting another broker.
    for binding in bindings:
        address = binding.get("HostIp", "")
        if binding["HostPort"] == "8883" and ":" not in address:
            address = "127.0.0.1" if address in ("", "0.0.0.0") else address
            socket.inet_pton(socket.AF_INET, address)
            return (address, 8883)
    raise RenewalError("No supported local IPv4 TLS 8883 binding; no certificate changed")


def signal_reload(broker_id):
    subprocess.run(["docker", "kill", "--signal", "HUP", broker_id], check=True,
                   capture_output=True, timeout=10)


def activate_release(release, fingerprint, broker_id=None, timeout=20):
    """Caller holds exclusive_lock through promotion, verification and recovery."""
    if not release.is_dir() or release.resolve().parent != (TLS_DIR / "releases").resolve():
        raise RenewalError("Candidate is outside the managed releases directory")
    previous = previous_current(required=broker_id is not None)
    endpoint = broker_tls_endpoint(broker_id) if broker_id else None
    old_fingerprint = certificate_fingerprint(TLS_DIR / "current/fullchain.pem") if broker_id else None
    atomic_current(Path("releases") / release.name)
    if not broker_id:
        return
    try:
        signal_reload(broker_id)
        verify_served_certificate(endpoint, fingerprint, timeout=timeout)
    except Exception:
        try:
            atomic_current(previous)  # Restore the exact previous symlink text.
        except Exception:
            raise RenewalError("TLS reload failed and current could not be restored; manual intervention required") from None
        try:
            signal_reload(broker_id)
            verify_served_certificate(endpoint, old_fingerprint, timeout=timeout)
        except Exception:
            raise RenewalError("Previous current restored, but previous TLS certificate recovery failed; manual intervention required") from None
        # Non-zero even after recovery: Certbot/operators must notice the failed renewal.
        raise RenewalError("TLS renewal failed; previous current and served certificate restored and verified") from None


def validate_bundle(cert, key, ca_file=None):
    cert, key = Path(cert), Path(key)
    # Loading into a server context verifies the private key matches the leaf.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    details = ssl._ssl._test_decode_cert(str(cert))
    now = datetime.now(timezone.utc).timestamp()
    if not ssl.cert_time_to_seconds(details["notBefore"]) <= now:
        raise ValueError("Certificate is not valid yet")
    if ssl.cert_time_to_seconds(details["notAfter"]) - now < 86400:
        raise ValueError("Certificate expires in less than 24 hours")
    hostname = subprocess.run(["openssl", "x509", "-in", str(cert), "-checkhost", DOMAIN, "-noout"], capture_output=True)
    if hostname.returncode:
        raise ValueError("Certificate does not cover the MQTT hostname")
    args = ["openssl", "verify", "-verify_hostname", DOMAIN, "-purpose", "sslserver",
            "-untrusted", str(cert)]
    if ca_file:
        args += ["-CAfile", str(ca_file)]
    args += [str(cert)]
    if subprocess.run(args, capture_output=True).returncode:
        raise ValueError("Certificate chain verification failed")
    leaf = subprocess.run(["openssl", "x509", "-in", str(cert), "-outform", "DER"], capture_output=True, check=True).stdout
    return hashlib.sha256(leaf).hexdigest()


def deployed_tls_broker():
    result = subprocess.run(["docker", "inspect", "iot-platform-mosquitto"], capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError("TLS broker is not deployed")
    broker = json.loads(result.stdout)[0]
    mounts = {m["Destination"]: m["Source"] for m in broker["Mounts"]}
    if (not broker["State"]["Running"]
            or not broker["HostConfig"]["PortBindings"].get("8883/tcp")
            or mounts.get("/mosquitto/tls") != str(TLS_DIR)
            or Path(mounts.get("/mosquitto/config/mosquitto.conf", "")).name != "mosquitto-tls.conf"):
        raise RuntimeError("Refusing to signal a broker without this deployed TLS configuration")
    return broker["Id"]


def stage_release(lineage, fingerprint):
    os.umask(0o077)
    releases = TLS_DIR / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    for directory in (TLS_DIR, releases):
        os.chown(directory, 0, 1883)
        directory.chmod(0o750)
    release = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=releases))
    os.chown(release, 0, 1883)
    release.chmod(0o750)
    for name in ("fullchain.pem", "privkey.pem"):
        target = release / name
        target.write_bytes((lineage / name).read_bytes())
        os.chown(target, 0, 1883)
        target.chmod(0o640)
    if validate_bundle(release / "fullchain.pem", release / "privkey.pem") != fingerprint:
        raise RuntimeError("Staged certificate differs from validated source; STOP")
    return release


def verification_timeout(value):
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Timeout must be a number") from None
    if not 1 <= seconds <= 120:
        raise argparse.ArgumentTypeError("Timeout must be between 1 and 120 seconds")
    return seconds


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--reload", action="store_true", help="only after authorized initial TLS deployment")
    parser.add_argument("--verify-timeout", type=verification_timeout, default=20,
                        help="TLS verification deadline in seconds, per attempt (default: 20; max: 120)")
    args = parser.parse_args(argv)
    if args.check_only and args.reload:
        parser.error("--check-only cannot be combined with --reload")
    lineage = Path(os.getenv("RENEWED_LINEAGE", "/etc/letsencrypt/live/" + DOMAIN)).resolve()
    expected = Path("/etc/letsencrypt/live/" + DOMAIN).resolve()
    if lineage != expected:
        print("Unrelated certificate lineage; nothing staged or signalled")
        return
    if os.geteuid() != 0:
        raise SystemExit("Run as root to read the private Certbot key")
    if args.check_only:
        validate_bundle(lineage / "fullchain.pem", lineage / "privkey.pem")
        print("Certificate hostname, dates, trusted chain and key match verified")
        return
    with exclusive_lock():
        fingerprint = validate_bundle(lineage / "fullchain.pem", lineage / "privkey.pem")
        broker_id = deployed_tls_broker() if args.reload else None
        previous_current(required=broker_id is not None)
        release = stage_release(lineage, fingerprint)
        activate_release(release, fingerprint, broker_id=broker_id, timeout=args.verify_timeout)
    print("TLS bundle staged atomically; previous releases retained")
    print("TLS certificate served on 8883 verified" if broker_id else "No broker signalled (staging only)")


if __name__ == "__main__":
    try:
        main()
    except RenewalError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print("TLS renewal stopped: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
