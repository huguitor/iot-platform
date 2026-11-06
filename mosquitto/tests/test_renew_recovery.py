"""Renewal state machine and real temporary TLS sockets; no active broker calls."""

from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("renew_recovery", Path(__file__).resolve().parents[1] / "tools/renew-tls.py")
renew = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renew)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="iot-renew-state-")
        self.root = Path(self.tmp.name)
        self.old = self.root / "releases/old"
        self.new = self.root / "releases/new"
        self.old.mkdir(parents=True)
        self.new.mkdir()
        (self.old / "fullchain.pem").write_text("old fixture certificate")
        (self.new / "fullchain.pem").write_text("new fixture certificate")
        (self.root / "current").symlink_to("releases/old")
        self.directory = patch.object(renew, "TLS_DIR", self.root)
        self.directory.start()
        self.endpoint = patch.object(renew, "broker_tls_endpoint", return_value=("127.0.0.1", 18883))
        self.endpoint.start()
        self.fingerprint = patch.object(renew, "certificate_fingerprint", return_value="old-fingerprint")
        self.fingerprint.start()

    def tearDown(self):
        self.fingerprint.stop()
        self.endpoint.stop()
        self.directory.stop()
        self.tmp.cleanup()

    def activate(self):
        with renew.exclusive_lock():
            renew.activate_release(self.new, "new-fingerprint", broker_id="disposable-fixture", timeout=1)

    def test_success_promotes_and_verifies_exact_certificate(self):
        with patch.object(renew, "signal_reload") as signal, patch.object(renew, "verify_served_certificate") as verify:
            self.activate()
        self.assertEqual(os.readlink(self.root / "current"), "releases/new")
        signal.assert_called_once_with("disposable-fixture")
        verify.assert_called_once_with(("127.0.0.1", 18883), "new-fingerprint", timeout=1)
        self.assertTrue(self.old.is_dir())

    def test_signal_failure_restores_and_reloads_previous(self):
        with patch.object(renew, "signal_reload", side_effect=[RuntimeError("fixture failure"), None]) as signal, \
                patch.object(renew, "verify_served_certificate") as verify:
            with self.assertRaisesRegex(renew.RenewalError, "restored and verified"):
                self.activate()
        self.assertEqual(os.readlink(self.root / "current"), "releases/old")
        self.assertEqual(signal.call_count, 2)
        verify.assert_called_once_with(("127.0.0.1", 18883), "old-fingerprint", timeout=1)

    def test_wrong_served_certificate_recovers_and_reports_failure(self):
        with patch.object(renew, "signal_reload") as signal, \
                patch.object(renew, "verify_served_certificate", side_effect=[renew.RenewalError("timeout"), None]) as verify:
            with self.assertRaisesRegex(renew.RenewalError, "restored and verified"):
                self.activate()
        self.assertEqual(os.readlink(self.root / "current"), "releases/old")
        self.assertEqual(signal.call_count, 2)
        self.assertEqual([c.args[1] for c in verify.call_args_list], ["new-fingerprint", "old-fingerprint"])

    def test_failed_recovery_preserves_old_link_and_reports_manual_action(self):
        with patch.object(renew, "signal_reload"), \
                patch.object(renew, "verify_served_certificate", side_effect=renew.RenewalError("timeout")):
            with self.assertRaisesRegex(renew.RenewalError, "recovery failed; manual intervention"):
                self.activate()
        self.assertEqual(os.readlink(self.root / "current"), "releases/old")
        self.assertTrue(self.new.is_dir())

    def test_restore_link_failure_does_not_signal_again(self):
        real_atomic = renew.atomic_current
        def atomic(target):
            if str(target) == "releases/old":
                raise OSError("fixture restore failure")
            real_atomic(target)
        with patch.object(renew, "atomic_current", side_effect=atomic), \
                patch.object(renew, "signal_reload", side_effect=RuntimeError("fixture failure")) as signal:
            with self.assertRaisesRegex(renew.RenewalError, "current could not be restored"):
                self.activate()
        self.assertEqual(signal.call_count, 1)

    def test_missing_previous_link_prevents_promotion_or_signal(self):
        (self.root / "current").unlink()
        with patch.object(renew, "signal_reload") as signal:
            with self.assertRaises(renew.RenewalError):
                self.activate()
        self.assertFalse((self.root / "current").exists())
        signal.assert_not_called()

    def test_absolute_previous_symlink_is_restored_verbatim(self):
        (self.root / "current").unlink()
        (self.root / "current").symlink_to(str(self.old))
        with patch.object(renew, "signal_reload", side_effect=[RuntimeError("fixture failure"), None]), \
                patch.object(renew, "verify_served_certificate"):
            with self.assertRaisesRegex(renew.RenewalError, "restored and verified"):
                self.activate()
        self.assertEqual(os.readlink(self.root / "current"), str(self.old))

    def test_staging_only_does_not_contact_or_signal_broker(self):
        with patch.object(renew, "signal_reload") as signal, patch.object(renew, "verify_served_certificate") as verify:
            with renew.exclusive_lock():
                renew.activate_release(self.new, "new-fingerprint")
        self.assertEqual(os.readlink(self.root / "current"), "releases/new")
        signal.assert_not_called()
        verify.assert_not_called()

    def test_lock_excludes_another_process_and_is_reusable(self):
        code = ("import os,fcntl,sys; f=os.open(sys.argv[1],os.O_RDWR);\n"
                "try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
                "except BlockingIOError: sys.exit(0)\n"
                "sys.exit(1)")
        with renew.exclusive_lock():
            child = subprocess.run([sys.executable, "-B", "-c", code, str(self.root / ".renew.lock")],
                                   capture_output=True, timeout=3)
            self.assertEqual(child.returncode, 0)
            with self.assertRaisesRegex(renew.RenewalError, "Another TLS renewal"):
                with renew.exclusive_lock():
                    self.fail("second lock unexpectedly acquired")
        with renew.exclusive_lock():
            pass
        self.assertEqual((self.root / ".renew.lock").stat().st_mode & 0o777, 0o600)

    def test_lock_released_after_exception(self):
        with self.assertRaises(RuntimeError):
            with renew.exclusive_lock():
                raise RuntimeError("fixture failure")
        with renew.exclusive_lock():
            pass

    def test_unsafe_lock_file_is_rejected(self):
        lock = self.root / ".renew.lock"
        lock.write_text("fixture")
        lock.chmod(0o644)
        with self.assertRaisesRegex(renew.RenewalError, "Unsafe"):
            with renew.exclusive_lock():
                self.fail("unsafe lock acquired")

    def test_check_only_still_has_no_staging_lock_or_signal(self):
        with patch.object(renew.Path, "resolve", return_value=self.root), \
                patch.object(renew.os, "geteuid", return_value=0), \
                patch.object(renew, "validate_bundle", return_value="fixture-fingerprint") as validate, \
                patch.object(renew, "exclusive_lock", side_effect=AssertionError("unexpected lock")), \
                patch.object(renew, "stage_release", side_effect=AssertionError("unexpected stage")), \
                redirect_stdout(io.StringIO()):
            renew.main(["--check-only"])
        self.assertEqual(validate.call_count, 1)

    def test_timeout_arguments_are_bounded(self):
        for value in ("0", "121", "nan", "inf", "invalid"):
            with self.subTest(value=value), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    renew.main(["--verify-timeout", value])
                self.assertEqual(failure.exception.code, 2)


class ActualTLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="iot-renew-tls-socket-")
        cls.root = Path(cls.tmp.name)
        key, cert = cls.root / "ca.key", cls.root / "ca.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "3", "-subj", "/CN=Disposable Renewal CA",
                        "-addext", "basicConstraints=critical,CA:TRUE",
                        "-addext", "keyUsage=critical,keyCertSign,cRLSign"], check=True, capture_output=True)
        extensions = cls.root / "extensions"
        extensions.write_text("subjectAltName=DNS:" + renew.DOMAIN + "\nextendedKeyUsage=serverAuth\n"
                              "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                              "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n")
        for serial, name in enumerate(("server", "new"), 1):
            subprocess.run(["openssl", "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                            "-nodes", "-keyout", str(cls.root / (name + ".key")), "-out", str(cls.root / (name + ".csr")),
                            "-subj", "/CN=" + renew.DOMAIN], check=True, capture_output=True)
            subprocess.run(["openssl", "x509", "-req", "-in", str(cls.root / (name + ".csr")), "-CA", str(cert),
                            "-CAkey", str(key), "-set_serial", str(serial), "-days", "2", "-extfile", str(extensions),
                            "-out", str(cls.root / (name + ".pem"))], check=True, capture_output=True)
        cls.server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_context.load_cert_chain(cls.root / "server.pem", cls.root / "server.key")
        cls.new_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.new_context.load_cert_chain(cls.root / "new.pem", cls.root / "new.key")
        cls.client_context = ssl.create_default_context(cafile=str(cert))
        cls.fingerprint = renew.certificate_fingerprint(cls.root / "server.pem")
        cls.new_fingerprint = renew.certificate_fingerprint(cls.root / "new.pem")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.05)
        self.endpoint = self.listener.getsockname()
        self.stop = threading.Event()
        def serve():
            while not self.stop.is_set():
                try:
                    raw, _ = self.listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    raw.settimeout(0.2)
                    with self.server_context.wrap_socket(raw, server_side=True):
                        pass
                except (OSError, ssl.SSLError):
                    raw.close()
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=1)
        self.assertFalse(self.thread.is_alive())

    def test_expected_certificate_verified_over_a_real_tls_connection(self):
        renew.verify_served_certificate(self.endpoint, self.fingerprint, timeout=1, context=self.client_context)

    def test_wrong_certificate_times_out_within_bounded_window(self):
        start = time.monotonic()
        with self.assertRaisesRegex(renew.RenewalError, "Timed out"):
            renew.verify_served_certificate(self.endpoint, "wrong-fingerprint", timeout=0.2, context=self.client_context)
        self.assertLess(time.monotonic() - start, 0.8)

    def test_untrusted_server_does_not_pass_verification(self):
        with self.assertRaises(renew.RenewalError):
            renew.verify_served_certificate(self.endpoint, self.fingerprint, timeout=0.1)

    def run_real_activation(self, fail_new=False):
        with tempfile.TemporaryDirectory(prefix="iot-renew-real-coordinator-") as folder:
            directory = Path(folder)
            for name, source in (("old", "server"), ("new", "new")):
                release = directory / "releases" / name
                release.mkdir(parents=True)
                (release / "fullchain.pem").write_bytes((self.root / (source + ".pem")).read_bytes())
                (release / "privkey.pem").write_bytes((self.root / (source + ".key")).read_bytes())
            (directory / "current").symlink_to("releases/old")
            calls = []
            def signal(broker_id):
                calls.append(broker_id)
                selected = os.readlink(directory / "current")
                if selected == "releases/new" and not fail_new:
                    self.server_context = self.new_context
                elif selected == "releases/old":
                    self.server_context = type(self).server_context
            def verify(endpoint, fingerprint, timeout):
                original(endpoint, fingerprint, timeout=timeout, context=self.client_context)
            original = renew.verify_served_certificate
            with patch.object(renew, "TLS_DIR", directory), patch.object(renew, "signal_reload", side_effect=signal), \
                    patch.object(renew, "broker_tls_endpoint", return_value=self.endpoint), \
                    patch.object(renew, "verify_served_certificate", side_effect=verify):
                with renew.exclusive_lock():
                    if fail_new:
                        with self.assertRaisesRegex(renew.RenewalError, "restored and verified"):
                            renew.activate_release(directory / "releases/new", self.new_fingerprint, "fixture-only", timeout=0.15)
                    else:
                        renew.activate_release(directory / "releases/new", self.new_fingerprint, "fixture-only", timeout=1)
            self.assertEqual(os.readlink(directory / "current"), "releases/old" if fail_new else "releases/new")
            self.assertEqual(len(calls), 2 if fail_new else 1)

    def test_coordinator_success_with_real_new_tls_certificate(self):
        self.run_real_activation()

    def test_coordinator_timeout_and_recovery_verified_over_real_tls(self):
        self.run_real_activation(fail_new=True)


if __name__ == "__main__":
    unittest.main()
