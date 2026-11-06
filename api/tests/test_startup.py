"""Prueba el hook real aislado, sin importar la app ni crear conexiones."""

import ast
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch


class StartupTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / "app" / "main.py"
        tree = ast.parse(source.read_text())
        hook = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "on_startup"
        )
        # Evita importar dependencias y registrar/iniciar FastAPI.
        hook.decorator_list = []
        self.base = MagicMock()
        self.engine = MagicMock()
        self.sessions = MagicMock()
        self.auth = MagicMock()
        namespace = {
            "os": os,
            "Base": self.base,
            "engine": self.engine,
            "SessionLocal": self.sessions,
            "auth": self.auth,
            "text": lambda sql: sql,
        }
        module = ast.Module(body=[hook], type_ignores=[])
        exec(compile(module, str(source), "exec"), namespace)
        self.startup = namespace["on_startup"]

    def test_read_only_skips_all_initialization(self):
        for value in ("true", "1", "yes", "on", " TRUE "):
            with self.subTest(value=value), patch.dict(
                os.environ, {"API_STARTUP_READ_ONLY": value}, clear=True
            ):
                self.startup()
                self.base.metadata.create_all.assert_not_called()
                self.engine.begin.assert_not_called()
                self.sessions.assert_not_called()
                self.auth.ensure_admin_user.assert_not_called()

    def test_default_and_disabled_preserve_initialization(self):
        for value in (None, "false", "0", "no", "off", ""):
            with self.subTest(value=value):
                self.setUp()
                env = {} if value is None else {"API_STARTUP_READ_ONLY": value}
                with patch.dict(os.environ, env, clear=True):
                    self.startup()
                self.base.metadata.create_all.assert_called_once_with(bind=self.engine)
                self.engine.begin.assert_called_once_with()
                connection = self.engine.begin.return_value.__enter__.return_value
                statements = [call.args[0].strip() for call in connection.execute.call_args_list]
                self.assertEqual(len(statements), 7)
                self.assertEqual(sum(sql.startswith("ALTER TABLE devices") for sql in statements), 6)
                self.assertTrue(statements[-1].startswith("CREATE INDEX IF NOT EXISTS"))
                self.sessions.assert_called_once_with()
                db = self.sessions.return_value.__enter__.return_value
                self.auth.ensure_admin_user.assert_called_once_with(db)


if __name__ == "__main__":
    unittest.main()
