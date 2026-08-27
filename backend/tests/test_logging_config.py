import os
import unittest
from unittest.mock import patch

from esg_encoding.logging_config import env_float


class LoggingConfigTests(unittest.TestCase):
    def test_env_float_uses_default_for_invalid_value(self):
        with patch.dict(os.environ, {"APP_SLOW_REQUEST_MS": "invalid"}):
            self.assertEqual(env_float("APP_SLOW_REQUEST_MS", 2000, 0), 2000.0)

    def test_env_float_applies_minimum(self):
        with patch.dict(os.environ, {"APP_SLOW_REQUEST_MS": "-10"}):
            self.assertEqual(env_float("APP_SLOW_REQUEST_MS", 2000, 0), 0.0)
