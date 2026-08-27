import importlib.util
import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "start_backend.py"
SPEC = importlib.util.spec_from_file_location("start_backend_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
start_backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(start_backend)


class StartBackendTests(unittest.TestCase):
    def test_parse_port_validates_the_configured_value(self):
        self.assertEqual(start_backend._parse_port("8123"), 8123)
        for invalid in ("invalid", "0", "65536"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                start_backend._parse_port(invalid)

    def test_classifies_an_existing_healthy_backend_before_binding(self):
        with (
            patch.object(start_backend, "_probe_existing_backend", return_value=True),
            patch.object(start_backend, "_bind_error") as bind_error,
        ):
            state, error = start_backend._classify_endpoint("0.0.0.0", 8000)

        self.assertEqual(state, "existing_backend")
        self.assertIsNone(error)
        bind_error.assert_not_called()

    def test_reports_an_unrelated_port_conflict(self):
        conflict = OSError("address already in use")
        with (
            patch.object(start_backend, "_probe_existing_backend", return_value=False),
            patch.object(start_backend, "_bind_error", return_value=conflict),
        ):
            state, error = start_backend._classify_endpoint(
                "0.0.0.0",
                8000,
                health_attempts=1,
            )

        self.assertEqual(state, "occupied")
        self.assertIn("address already in use", error or "")

    def test_main_does_not_print_the_api_key_when_backend_already_runs(self):
        secret = "do-not-print-this-secret"
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "API_HOST": "0.0.0.0",
                    "API_PORT": "8000",
                    "LLM_API_KEY": secret,
                },
                clear=False,
            ),
            patch.object(start_backend, "load_dotenv"),
            patch.object(
                start_backend,
                "_classify_endpoint",
                return_value=("existing_backend", None),
            ),
            redirect_stdout(output),
        ):
            exit_code = start_backend.main()

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("后端已经", rendered)
        self.assertIn("LLM API Key: 已配置", rendered)
        self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
