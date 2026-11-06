"""Fictitious credentials only; no SSH, privileged reads or real config access."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/transfer-lab-credentials.py'
spec = importlib.util.spec_from_file_location('transfer_lab', SCRIPT)
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)
FIXTURE = {'username': 'lab-esp8266-dell-01', 'device_id': 'lab-esp8266-dell-20261104-01',
           'password': 'FICTIONAL-password-"\\?'}
CONFIG = '''#pragma once
const char DEVICE_ID[] = "old"; // device comment
const char MQTT_HOST[] = "old.local";
const uint16_t MQTT_PORT = 1883;
const char MQTT_USER[] = "old-user";
const char MQTT_PASS[] = "old-fictitious-password";
const char AP_PASS[] = "FICTIONAL-AP-unchanged";
#define WIFI_DEFAULT_SSID "FICTIONAL-WIFI"
#define WIFI_DEFAULT_PASS "FICTIONAL-WIFI-PASS"
'''


class TransferTests(unittest.TestCase):
    def test_source_read_only_and_private_mode_required(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'fixture.json'
            path.write_text(json.dumps(FIXTURE)); path.chmod(0o600)
            original = path.read_bytes()
            original_mode = path.stat().st_mode
            with patch.object(transfer, 'SOURCE', path), \
                    patch.object(transfer.os, 'fstat', return_value=SimpleNamespace(st_uid=0, st_mode=original_mode)):
                self.assertEqual(transfer.load_record(), FIXTURE)
            with patch.object(transfer, 'SOURCE', path), \
                    patch.object(transfer.os, 'fstat', return_value=SimpleNamespace(st_uid=0, st_mode=original_mode | 0o040)):
                with self.assertRaises(ValueError):
                    transfer.load_record()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(path.stat().st_mode, original_mode)

    def test_nonroot_refused_without_read_or_connection(self):
        with patch.object(transfer.os, 'geteuid', return_value=1000), \
                patch.object(transfer, 'load_record', side_effect=AssertionError), \
                patch.object(transfer.subprocess, 'run', side_effect=AssertionError), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(transfer.main([]), 1)
            self.assertIn('E_ROOT:', output.getvalue())

    def test_source_access_error_has_safe_code(self):
        with patch.object(transfer.os, 'open', side_effect=PermissionError(FIXTURE['password'])):
            with self.assertRaises(transfer.Failure) as result:
                transfer.load_record()
            self.assertEqual(result.exception.code, 'E_SOURCE_ACCESS')
            self.assertNotIn(FIXTURE['password'], str(result.exception))

    def test_remote_allowlisted_error_is_reported(self):
        with patch.object(transfer.os, 'geteuid', return_value=0), \
                patch.object(transfer, 'load_record', return_value=FIXTURE), \
                patch.object(transfer.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 1, b'ERROR:E_DEST_DIR\n', FIXTURE['password'].encode())), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(transfer.main(['--check-only']), 1)
            self.assertIn('E_DEST_DIR:', output.getvalue())
            self.assertNotIn(FIXTURE['password'], output.getvalue())

    def test_remote_arbitrary_output_is_never_reported(self):
        for payload in (FIXTURE['password'].encode(), b'ERROR:E_DEST_DIR\n' + FIXTURE['password'].encode()):
            with patch.object(transfer.os, 'geteuid', return_value=0), \
                    patch.object(transfer, 'load_record', return_value=FIXTURE), \
                    patch.object(transfer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, payload, payload)), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(transfer.main(['--check-only']), 1)
                self.assertIn('E_SSH:', output.getvalue())
                self.assertNotIn(FIXTURE['password'], output.getvalue())

    def test_timeout_never_exposes_command_or_input(self):
        with patch.object(transfer.os, 'geteuid', return_value=0), \
                patch.object(transfer, 'load_record', return_value=FIXTURE), \
                patch.object(transfer.subprocess, 'run', side_effect=subprocess.TimeoutExpired(
                    FIXTURE['password'], 1, output=FIXTURE['password'], stderr=FIXTURE['password'])), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(transfer.main(['--check-only']), 1)
            self.assertIn('E_TIMEOUT:', output.getvalue())
            self.assertNotIn(FIXTURE['password'], output.getvalue())

    def test_group_writable_directory_fails_without_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder); directory.chmod(0o775)
            path = directory/'config.h'; path.write_text(CONFIG)
            with self.assertRaises(transfer.Failure) as result:
                transfer.update(path, FIXTURE, True, os.geteuid())
            self.assertEqual(result.exception.code, 'E_DEST_DIR')
            self.assertEqual(path.read_text(), CONFIG)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o775)

    def test_validate_rejects_invalid_structures(self):
        for value in ([], {}, dict(FIXTURE, extra=1), dict(FIXTURE, username='other'),
                      dict(FIXTURE, device_id='other'), dict(FIXTURE, password=''),
                      dict(FIXTURE, password='bad\nvalue'), dict(FIXTURE, password=123)):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                transfer.validate(value)

    def test_duplicate_json_fields(self):
        with self.assertRaises(ValueError):
            json.loads('{"password":"a","password":"b"}', object_pairs_hook=transfer.unique_object)

    def test_only_initializers_change_and_cpp_escaping(self):
        text = transfer.render(CONFIG, FIXTURE)
        self.assertIn('"iot.panozoelectronica.com.ar"', text)
        self.assertIn('MQTT_PORT = 8883;', text)
        self.assertIn('// device comment', text)
        self.assertEqual(text.split('const char AP_PASS')[1], CONFIG.split('const char AP_PASS')[1])
        self.assertIn('\\"', text)
        self.assertIn('\\?', text)

    def test_crlf_preserved(self):
        result = transfer.render(CONFIG.replace('\n', '\r\n'), FIXTURE)
        self.assertEqual(result.count('\r\n'), CONFIG.count('\n'))

    def test_bad_or_duplicate_declaration_rejected(self):
        for text in (CONFIG.replace('MQTT_USER', 'MISSING'), CONFIG + 'const char MQTT_USER[]="x";\n',
                     '#if 0\n' + CONFIG + '#endif\n',
                     CONFIG.replace('const uint16_t MQTT_PORT = 1883;', '#define MQTT_PORT 1883')):
            with self.assertRaises(ValueError):
                transfer.render(text, FIXTURE)

    def test_atomic_update_and_mode_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.h'
            path.write_text(CONFIG)
            path.chmod(0o664)
            inode = path.stat().st_ino
            transfer.update(path, FIXTURE, False, os.geteuid())
            self.assertNotEqual(inode, path.stat().st_ino)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_uid, os.geteuid())
            self.assertEqual(list(Path(folder).iterdir()), [path])
            self.assertIn('MQTT_PORT = 8883;', path.read_text())

    def test_check_only_writes_nothing_including_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.h'
            path.write_text(CONFIG)
            path.chmod(0o664)
            before = path.stat()
            transfer.update(path, FIXTURE, True, os.geteuid())
            self.assertEqual(path.read_text(), CONFIG)
            self.assertEqual(path.stat(), before)

    def test_symlink_and_hardlink_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'original'; path.write_text(CONFIG)
            link = Path(folder)/'config.h'; link.symlink_to(path)
            with self.assertRaises(OSError):
                transfer.update(link, FIXTURE, False, os.geteuid())
            link.unlink(); os.link(path, link)
            with self.assertRaises(ValueError):
                transfer.update(link, FIXTURE, False, os.geteuid())

    def test_failed_replace_keeps_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.h'; path.write_text(CONFIG)
            with patch.object(transfer.os, 'replace', side_effect=OSError('fictional failure')):
                with self.assertRaises(OSError):
                    transfer.update(path, FIXTURE, False, os.geteuid())
            self.assertEqual(path.read_text(), CONFIG)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_help_does_not_read_or_connect(self):
        with patch.object(transfer, 'load_record', side_effect=AssertionError), \
                patch.object(transfer.subprocess, 'run', side_effect=AssertionError), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                transfer.main(['--help'])
            self.assertEqual(result.exception.code, 0)

    def test_stdin_only_and_ssh_runs_as_hugo(self):
        for args, expected in (([], b'UPDATE_OK\n'), (['--check-only'], b'CHECK_OK\n')):
            with patch.object(transfer.os, 'geteuid', return_value=0), \
                    patch.object(transfer, 'load_record', return_value=FIXTURE), \
                    patch.object(transfer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, expected)) as run, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(transfer.main(args), 0)
                command = run.call_args.args[0]
                self.assertEqual(command[:5], ['/usr/sbin/runuser', '-u', 'hugo', '--', '/usr/bin/ssh'])
                self.assertNotIn(FIXTURE['password'], str(command))
                self.assertEqual(json.loads(run.call_args.kwargs['input']), FIXTURE)
                self.assertNotIn(FIXTURE['password'], output.getvalue())

    def test_exceptions_and_remote_output_never_printed(self):
        with patch.object(transfer.os, 'geteuid', return_value=0), \
                patch.object(transfer, 'load_record', return_value=FIXTURE), \
                patch.object(transfer.subprocess, 'run', side_effect=RuntimeError(FIXTURE['password'])), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(transfer.main([]), 1)
            self.assertNotIn(FIXTURE['password'], output.getvalue())

    def test_generated_remote_program_with_local_fixture_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.h'; path.write_text(CONFIG)
            (Path(folder)/'.gitignore').write_text('config.h\n')
            subprocess.run(['git', 'init', '-q', '-b', transfer.BRANCH, folder], check=True)
            for check in (True, False):
                with patch.object(transfer, 'TARGET', str(path)):
                    code = transfer.remote_code(check)
                result = subprocess.run(['python3', '-c', code], input=json.dumps(FIXTURE).encode(), capture_output=True)
                self.assertEqual(result.returncode, 0, 'Remote fixture failed (output suppressed)')
                self.assertEqual(result.stdout, b'CHECK_OK\n' if check else b'UPDATE_OK\n')
                self.assertEqual(path.read_text(), CONFIG if check else transfer.render(CONFIG, FIXTURE))


if __name__ == '__main__':
    unittest.main()
