"""Certificate/provisioning checks with temporary keys only; no active services."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renew = load("renew_tls", "renew-tls.py")
provision = load("provision_public", "provision-public-auth.py")


class CertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="iot-certificate-unit-")
        cls.path = Path(cls.tmp.name)
        def run(*args):
            subprocess.run(args, check=True, capture_output=True)
        p = cls.path
        run("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-nodes", "-keyout", str(p / "ca.key"), "-out", str(p / "ca.pem"), "-days", "3", "-subj", "/CN=Disposable CA")
        run("openssl", "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-nodes", "-keyout", str(p / "server.key"), "-out", str(p / "server.csr"), "-subj", "/CN=" + renew.DOMAIN)
        (p / "extensions").write_text("subjectAltName=DNS:" + renew.DOMAIN + "\nextendedKeyUsage=serverAuth\n")
        run("openssl", "x509", "-req", "-in", str(p / "server.csr"), "-CA", str(p / "ca.pem"),
            "-CAkey", str(p / "ca.key"), "-CAcreateserial", "-out", str(p / "server.pem"),
            "-days", "2", "-extfile", str(p / "extensions"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def validate(self):
        return renew.validate_bundle(self.path / "server.pem", self.path / "server.key", self.path / "ca.pem")

    def test_valid_chain_hostname_dates_and_key(self):
        self.assertEqual(len(self.validate()), 64)

    def test_wrong_key_rejected(self):
        with self.assertRaises(ssl.SSLError):
            renew.validate_bundle(self.path / "server.pem", self.path / "ca.key", self.path / "ca.pem")

    def test_wrong_hostname_rejected(self):
        with patch.object(renew, "DOMAIN", "wrong.invalid"):
            with self.assertRaises(ValueError):
                self.validate()

    def test_untrusted_chain_rejected(self):
        with self.assertRaises(ValueError):
            renew.validate_bundle(self.path / "server.pem", self.path / "server.key")

    def test_expiry_rejected_before_staging(self):
        with patch.object(renew, "datetime") as clock:
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(days=4)
            with self.assertRaisesRegex(ValueError, "expires"):
                self.validate()

    def test_reload_guard_rejects_non_tls_deployment(self):
        broker = {"State": {"Running": True}, "HostConfig": {"PortBindings": {"1883/tcp": []}},
                  "Mounts": [], "Id": "fixture-only"}
        response = subprocess.CompletedProcess([], 0, stdout=json.dumps([broker]).encode())
        with patch.object(renew.subprocess, "run", return_value=response) as run:
            with self.assertRaises(RuntimeError):
                renew.deployed_tls_broker()
            self.assertEqual(run.call_count, 1)  # inspect only, never a signal

    def test_provision_is_private_non_overwriting_and_avoids_secret_argv(self):
        with tempfile.TemporaryDirectory(prefix="iot-auth-unit-") as folder:
            base = Path(folder) / "mqtt"
            secret = "disposable-fixture-password"
            def offline_command(args, **kwargs):
                self.assertNotIn(secret, args)
                if args[:2] == ["docker", "exec"]:
                    return subprocess.CompletedProcess(args, 0, stdout=b"internal-fixture:$7$fixture-hash\n")
                if "mosquitto_passwd" in args:
                    (base / "auth/passwordfile").write_text(provision.USERNAME + ":$7$fixture-hash\n")
                return subprocess.CompletedProcess(args, 0)
            with patch.object(provision, "BASE", base), patch.object(provision.os, "geteuid", return_value=0), \
                    patch.object(provision.os, "chown"), patch.object(provision.secrets, "token_urlsafe", return_value=secret), \
                    patch.object(provision.subprocess, "run", side_effect=offline_command):
                output = io.StringIO()
                with redirect_stdout(output):
                    provision.main([])
                self.assertNotIn(secret, output.getvalue())
                self.assertEqual((base / "lab-device-credential.json").stat().st_mode & 0o777, 0o600)
                self.assertEqual((base / "auth/passwordfile").stat().st_mode & 0o777, 0o640)
                before = (base / "auth/passwordfile").read_bytes()
                with self.assertRaises(SystemExit):
                    provision.main([])
                self.assertEqual((base / "auth/passwordfile").read_bytes(), before)

    def test_provision_help_performs_no_actions(self):
        with patch.object(provision.os, "geteuid", side_effect=AssertionError("unexpected privilege check")), \
                patch.object(provision.subprocess, "run", side_effect=AssertionError("unexpected process")), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                provision.main(["--help"])
        self.assertEqual(result.exception.code, 0)

    def test_hash_generation_in_readonly_docker_with_writable_tmp(self):
        # Only disposable credentials; no active account or password file touched.
        with tempfile.TemporaryDirectory(prefix="iot-hash-readonly-") as folder:
            path = Path(folder) / "passwordfile"
            path.write_text("readonly-fixture:disposable-password\n")
            path.chmod(0o600)
            provision.hash_passwordfile(Path(folder), user=f"{os.getuid()}:{os.getgid()}")
            value = path.read_text()
            self.assertTrue(value.startswith("readonly-fixture:$"))
            self.assertNotIn("disposable-password", value)


if __name__ == "__main__":
    unittest.main()
