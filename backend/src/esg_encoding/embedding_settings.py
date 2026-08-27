"""Single-source model settings for embeddings and reranking.

Only two model-name switches are supported:
- EMBEDDING_MODEL
- RERANK_MODEL

Local HuggingFace cache discovery is handled internally by shared_embedding_model.py
from the selected model name. This file does not expose separate local path,
repo id, model id, or alias settings.
"""

from __future__ import annotations

import os

DEFAULT_EMBEDDING_MODEL_NAME = "microsoft/harrier-oss-v1-0.6b"
DEFAULT_RERANK_MODEL_NAME = "microsoft/harrier-oss-v1-0.6b"

DEFAULT_EMBEDDING_QUERY_PROMPT = (
    "Instruct: Retrieve the most relevant evidence passages from the ESG report for the given SASB/ESG disclosure metric, "
    "including directly matching values, units, time periods, scope, methodology, and surrounding evidence.\n"
    "Query: "
)

_MODEL_DTYPE_ALIASES = {
    "float32": "float32",
    "fp32": "float32",
    "full": "float32",
    "float": "float32",
    "bfloat16": "bfloat16",
    "bf16": "bfloat16",
    "float16": "float16",
    "fp16": "float16",
    "half": "float16",
    "auto": "auto",
}


def normalize_model_dtype(value: str | None, default: str = "float32") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raw = str(default or "float32").strip().lower()
    return _MODEL_DTYPE_ALIASES.get(raw, _MODEL_DTYPE_ALIASES.get(str(default).strip().lower(), "float32"))


def get_configured_embedding_model_name(default: str = DEFAULT_EMBEDDING_MODEL_NAME) -> str:
    """Return the single configured embedding model name."""
    return str(os.getenv("EMBEDDING_MODEL", "") or default).strip() or default


def get_configured_rerank_model_name(default: str = DEFAULT_RERANK_MODEL_NAME) -> str:
    """Return the single configured reranker model name."""
    return str(os.getenv("RERANK_MODEL", "") or default).strip() or default


def get_configured_embedding_model_dtype(default: str = "float32") -> str:
    return normalize_model_dtype(os.getenv("EMBEDDING_MODEL_DTYPE"), default)


def get_configured_rerank_model_dtype(default: str = "float32") -> str:
    return normalize_model_dtype(os.getenv("RERANK_MODEL_DTYPE"), default)


def get_embedding_query_prompt() -> str:
    return str(os.getenv("EMBEDDING_QUERY_PROMPT", "") or DEFAULT_EMBEDDING_QUERY_PROMPT)


def should_instruction_prompt_queries(model_name_or_path: str | None = None) -> bool:
    flag = str(os.getenv("EMBEDDING_QUERY_PROMPT_ENABLED", "1") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    model = str(model_name_or_path or get_configured_embedding_model_name() or "").lower()
    return "harrier-oss" in model or str(os.getenv("EMBEDDING_FORCE_QUERY_PROMPT", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
