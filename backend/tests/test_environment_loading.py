import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from esg_encoding import environment


class EnvironmentLoadingTests(unittest.TestCase):
    def test_default_env_path_is_anchored_to_the_backend_directory(self):
        with (
            patch.dict(environment.os.environ, {}, clear=True),
            patch.object(environment, "load_dotenv") as load_dotenv,
            patch.object(environment.Path, "is_file", return_value=True),
        ):
            loaded = environment.load_backend_environment()

        self.assertEqual(loaded, environment.DEFAULT_ENV_FILE.resolve())
        first_call = load_dotenv.call_args_list[0]
        self.assertEqual(
            first_call.kwargs["dotenv_path"],
            environment.DEFAULT_ENV_FILE.resolve(),
        )
        self.assertFalse(first_call.kwargs["override"])

    def test_explicit_env_file_takes_precedence(self):
        explicit = environment.BACKEND_DIR / "config" / "custom.env"
        with (
            patch.dict(
                environment.os.environ,
                {"ESG_ENV_FILE": str(explicit)},
                clear=True,
            ),
            patch.object(environment, "load_dotenv") as load_dotenv,
            patch.object(environment.Path, "is_file", return_value=True),
        ):
            loaded = environment.load_backend_environment()

        self.assertEqual(loaded, explicit.resolve())
        load_dotenv.assert_called_once_with(
            dotenv_path=explicit.resolve(),
            override=False,
        )


if __name__ == "__main__":
    unittest.main()
