from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from esg_encoding.shared_embedding_model import _looks_like_sentence_transformers_model


class ModelCacheTests(unittest.TestCase):
    def test_sentence_transformer_root_module_path_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            (model_dir / "1_Pooling").mkdir()
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "1_Pooling" / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "modules.json").write_text(
                json.dumps(
                    [
                        {
                            "idx": 0,
                            "name": "0",
                            "path": "",
                            "type": "sentence_transformers.models.Transformer",
                        },
                        {
                            "idx": 1,
                            "name": "1",
                            "path": "1_Pooling",
                            "type": "sentence_transformers.models.Pooling",
                        },
                        {
                            "idx": 2,
                            "name": "2",
                            "path": "2_Normalize",
                            "type": "sentence_transformers.models.Normalize",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            self.assertTrue(_looks_like_sentence_transformers_model(model_dir))


if __name__ == "__main__":
    unittest.main()
