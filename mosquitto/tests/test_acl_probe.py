"""Safe CLI/credential guards; broker delivery checks run in the isolated runner."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("acl_probe", TOOLS / "check-mqtt-acl.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeSafetyTests(unittest.TestCase):
    def test_help_has_no_credential_reads_or_network_actions(self):
        with patch.object(probe, "load_credentials", side_effect=AssertionError("unexpected read")), \
                patch.object(probe, "MQTT", side_effect=AssertionError("unexpected socket")), \
                patch.object(probe.subprocess, "run", side_effect=AssertionError("unexpected process")), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                probe.main(["--help"])
        self.assertEqual(result.exception.code, 0)

    def test_default_is_plan_without_accessing_secrets(self):
        output = io.StringIO()
        with patch.object(probe, "load_credentials", side_effect=AssertionError("unexpected read")), \
                patch.object(probe, "MQTT", side_effect=AssertionError("unexpected socket")), redirect_stdout(output):
            probe.main([])
        self.assertEqual(json.loads(output.getvalue())["mode"], "plan")

    def test_real_device_topic_is_refused_before_any_connection(self):
        with patch.object(probe, "MQTT", side_effect=AssertionError("unexpected socket")):
            with self.assertRaises(ValueError):
                probe.verify({}, "iot/devices/lab-esp8266-dell-20261104-01")

    def test_credential_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "credentials.json"
            path.write_text(json.dumps({role: {"username": role, "password": "fixture-secret"}
                                        for role in ("device", "observer")}))
            path.chmod(0o600)
            self.assertEqual(probe.load_credentials(path, fixture=True)["device"]["username"], "device")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                probe.load_credentials(path, fixture=True)

    def test_error_text_cannot_expose_credentials(self):
        output = io.StringIO()
        with patch.object(probe, "load_credentials", side_effect=ValueError("fixture-secret-never-print")), \
                patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=b"")), \
                redirect_stdout(output):
            with self.assertRaises(SystemExit):
                probe.main(["--run"])
        self.assertNotIn("fixture-secret", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["result"], "FAIL")


if __name__ == "__main__":
    unittest.main()
