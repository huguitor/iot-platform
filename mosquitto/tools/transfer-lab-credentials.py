#!/usr/bin/env python3
"""Root entry point; private JSON travels only over SSH stdin. No MQTT traffic."""
import argparse
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile

SOURCE = Path('/srv/iot-platform-secrets/mqtt/lab-device-credential.json')
TARGET = '/home/hugo/Arduino/esp8266_gateway_wiegand_mqtt/config.h'
BRANCH = 'feature/mqtt-tls-lab-1.6.0'

ERRORS = {'E_FIELDS': 'JSON: campos faltantes, adicionales o duplicados.',
 'E_IDENTITY': 'Identidad de laboratorio inesperada.',
 'E_PASSWORD': 'Formato de contraseña no admitido.',
 'E_ENCODING': 'Codificación de contraseña no admitida.',
 'E_CONFIG_CONDITIONAL': 'config.h contiene directivas condicionales no admitidas.',
 'E_CONFIG_AMBIGUOUS': 'config.h contiene campos ausentes o repetidos.',
 'E_CONFIG_DECLARATION': 'Declaración de config.h no admitida.',
 'E_DEST_FILE': 'Destino: propietario, tipo o cantidad de enlaces no admitidos.',
 'E_DEST_SIZE': 'config.h supera el tamaño permitido.',
 'E_DEST_DIR': 'Directorio destino: debe pertenecer a hugo y no permitir escritura a grupo u '
               'otros.',
 'E_CONCURRENT': 'config.h cambió durante la operación.',
 'E_REMOTE_USER': 'SSH remoto no se ejecuta como hugo.',
 'E_BRANCH': 'La rama remota no es la requerida.',
 'E_GIT_TRACKING': 'config.h debe estar ignorado y no seguido por Git.',
 'E_INPUT_SIZE': 'Entrada remota demasiado grande.',
 'E_SOURCE_MODE': 'Origen: debe ser regular, de root y sin permisos de grupo u otros.',
 'E_SOURCE_SIZE': 'JSON privado supera el tamaño permitido.',
 'E_ROOT': 'Ejecutar como root: se requiere leer el JSON privado y usar runuser.',
 'E_SSH': 'SSH o protocolo remoto falló; salida privada omitida.',
 'E_SOURCE_ACCESS': 'No se puede abrir o leer el JSON privado.',
 'E_JSON': 'JSON inválido.',
 'E_DEST_ACCESS': 'No se puede leer o preparar el destino.',
 'E_REMOTE_GIT': 'No se pudo consultar Git remoto.',
 'E_REMOTE': 'Validación u operación remota fallida.',
 'E_TIMEOUT': 'Tiempo de espera SSH agotado; no reintentar automáticamente.',
 'E_INTERNAL': 'Fallo interno; detalles omitidos para proteger secretos.'}


class Failure(ValueError):
    def __init__(self, code):
        self.code = code if code in ERRORS else "E_INTERNAL"
        super().__init__(self.code)



def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Failure('E_FIELDS')
        result[key] = value
    return result


def validate(record):
    if not isinstance(record, dict) or set(record) != {'username', 'device_id', 'password'}:
        raise Failure('E_FIELDS')
    if record['username'] != 'lab-esp8266-dell-01' or record['device_id'] != 'lab-esp8266-dell-20261104-01':
        raise Failure('E_IDENTITY')
    password = record['password']
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        raise Failure('E_PASSWORD')
    if any(ord(c) < 32 or ord(c) > 126 for c in password):
        raise Failure('E_ENCODING')
    return record


def render(text, record):
    validate(record)
    if re.search(r'^[ \t]*#[ \t]*(?:if|ifdef|ifndef|elif|else|endif)\b', text, re.MULTILINE):
        raise Failure('E_CONFIG_CONDITIONAL')
    values = {'DEVICE_ID': record['device_id'], 'MQTT_HOST': 'iot.panozoelectronica.com.ar',
              'MQTT_PORT': 8883, 'MQTT_USER': record['username'], 'MQTT_PASS': record['password']}
    # Accept only the project's unambiguous, single-line const declarations.
    # Preserve all bytes outside the initializer, including comments and CRLF.
    for name, value in values.items():
        declaration = (r'const[ \t]+uint16_t[ \t]+' + name if name == 'MQTT_PORT' else
                       r'const[ \t]+char[ \t]+' + name + r'[ \t]*\[[ \t]*\]')
        old_value = r'[0-9]+' if name == 'MQTT_PORT' else r'"(?:[^"\\\r\n]|\\[^\r\n])*"'
        pattern = re.compile(r'^([ \t]*' + declaration + r'[ \t]*=[ \t]*)(' + old_value +
                             r')([ \t]*;[^\r\n]*)(\r?)$', re.MULTILINE)
        # Refuse duplicate/conditional/macro declarations instead of guessing.
        if len(re.findall(r'\b' + name + r'\b', re.sub(r'//[^\r\n]*|/\*.*?\*/', '', text, flags=re.S))) != 1:
            raise Failure('E_CONFIG_AMBIGUOUS')
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise Failure('E_CONFIG_DECLARATION')
        replacement = str(value) if isinstance(value, int) else json.dumps(value, ensure_ascii=True)
        # C++ trigraphs must not transform private string contents on older toolchains.
        replacement = replacement.replace('?', '\\?')
        text = pattern.sub(lambda m: m[1] + replacement + m[3] + m[4], text)
    return text


def update(path, record, check_only, expected_uid):
    """Remote-only atomic write; caller is hugo. Never creates VPS secret files."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != expected_uid or before.st_nlink != 1:
            raise Failure('E_DEST_FILE')
        if before.st_size > 65536:
            raise Failure('E_DEST_SIZE')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            original = source.read(65537)
        prepared = render(original.decode('utf-8'), record).encode('utf-8')
        parent = Path(path).parent
        parent_stat = parent.stat()
        if parent_stat.st_uid != expected_uid or parent_stat.st_mode & 0o022:
            raise Failure('E_DEST_DIR')
        if check_only:
            return
        temporary = None
        try:
            out, temporary = tempfile.mkstemp(prefix='.config.h-', dir=parent)
            with os.fdopen(out, 'wb') as target:
                os.fchmod(target.fileno(), 0o600)
                target.write(prepared)
                target.flush()
                os.fsync(target.fileno())
            current = os.stat(path, follow_symlinks=False)
            if (current.st_ino, current.st_dev, current.st_mtime_ns, current.st_size) != (
                    before.st_ino, before.st_dev, before.st_mtime_ns, before.st_size):
                raise Failure('E_CONCURRENT')
            os.replace(temporary, path)
            temporary = None
            directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                os.unlink(temporary)
    finally:
        os.close(fd)


def remote_main(check_only):
    import pwd
    import sys
    try:
        account = pwd.getpwnam('hugo')
        if os.geteuid() != account.pw_uid:
            raise Failure('E_REMOTE_USER')
        repo = str(Path(TARGET).parent)
        branch = subprocess.run(['git', '-C', repo, 'branch', '--show-current'],
                                capture_output=True, check=True).stdout.decode().strip()
        if branch != BRANCH:
            raise Failure('E_BRANCH')
        ignored = subprocess.run(['git', '-C', repo, 'check-ignore', '-q', '--', 'config.h'],
                                 capture_output=True)
        tracked = subprocess.run(['git', '-C', repo, 'ls-files', '--', 'config.h'],
                                 capture_output=True, check=True)
        if ignored.returncode != 0 or tracked.stdout:
            raise Failure('E_GIT_TRACKING')
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            raise Failure('E_INPUT_SIZE')
        record = validate(json.loads(raw, object_pairs_hook=unique_object))
        update(TARGET, record, check_only, account.pw_uid)
        print('CHECK_OK' if check_only else 'UPDATE_OK')
    except Exception as error:
        code = error.code if isinstance(error, Failure) else (
            'E_JSON' if isinstance(error, (json.JSONDecodeError, UnicodeError)) else
            'E_REMOTE_GIT' if isinstance(error, subprocess.CalledProcessError) else
            'E_DEST_ACCESS' if isinstance(error, OSError) else 'E_REMOTE')
        print('ERROR:' + code)
        return 1
    return 0


def remote_code(check_only):
    imports = 'import json, os, re, stat, subprocess, tempfile\nfrom pathlib import Path\n'
    constants = 'ERRORS = ' + repr(ERRORS) + '\n' + ('class Failure(ValueError):\n' + inspect.getsource(Failure.__init__)) + '\nTARGET = ' + repr(TARGET) + '\nBRANCH = ' + repr(BRANCH) + '\n'
    functions = '\n'.join(inspect.getsource(f) for f in (unique_object, validate, render, update, remote_main))
    return imports + constants + functions + '\nraise SystemExit(remote_main(' + repr(check_only) + '))\n'


def load_record():
    try:
        fd = os.open(SOURCE, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise Failure('E_SOURCE_ACCESS') from None
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o077:
            raise Failure('E_SOURCE_MODE')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            raw = source.read(8193)
        if len(raw) > 8192:
            raise Failure('E_SOURCE_SIZE')
        try:
            return validate(json.loads(raw, object_pairs_hook=unique_object))
        except (json.JSONDecodeError, UnicodeError):
            raise Failure('E_JSON') from None
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true', help='Validate locally and remotely without writing')
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise Failure('E_ROOT')
        record = load_record()
        command = ['/usr/sbin/runuser', '-u', 'hugo', '--', '/usr/bin/ssh', '-T', '-o', 'BatchMode=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
                   'arduino-notebook', 'python3 -c ' + shlex.quote(remote_code(args.check_only))]
        result = subprocess.run(command, input=json.dumps(record).encode(), capture_output=True, timeout=45)
        expected = b'CHECK_OK\n' if args.check_only else b'UPDATE_OK\n'
        if result.returncode or result.stdout != expected:
            # Only accept exact tokens from our finite allowlist, never raw remote text.
            for code in ERRORS:
                if result.stdout == ('ERROR:' + code + '\n').encode():
                    raise Failure(code)
            raise Failure('E_SSH')
        print('Validación correcta; no se escribió ningún archivo.' if args.check_only else
              'Configuración privada actualizada; permisos 600, propietario hugo.')
        return 0
    except Exception as error:
        # No exception text, command, stderr or arbitrary remote stdout reaches output.
        code = error.code if isinstance(error, Failure) else (
            'E_TIMEOUT' if isinstance(error, subprocess.TimeoutExpired) else 'E_INTERNAL')
        print(code + ': ' + ERRORS[code])
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
