"""Validate and, when allowed, populate persistent backend model caches."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

from esg_encoding.embedding_settings import (
    get_configured_embedding_model_name,
    get_configured_rerank_model_name,
)
from esg_encoding.shared_embedding_model import prefer_local_model
from esg_encoding.retrieval.hipporag.settings import (
    HippoRAGSettings,
    resolve_hipporag_embedding_model_name,
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _valid_local_model(repo_id: str, hf_home: str) -> str | None:
    ref = prefer_local_model(repo_id, hf_home=hf_home)
    if not ref.local_path:
        return None

    model_dir = Path(ref.local_path)
    weights = [
        path
        for pattern in ("*.safetensors", "*.bin")
        for path in model_dir.rglob(pattern)
        if path.is_file()
    ]
    if not weights or any(path.stat().st_size < 1_000_000 for path in weights):
        return None
    return str(model_dir)


def _ensure_model(repo_id: str, hf_home: str, allow_online: bool) -> str:
    local_path = _valid_local_model(repo_id, hf_home)
    if local_path:
        print(f"[BackendModelPreflight] ready: {repo_id} -> {local_path}", flush=True)
        return local_path

    if not allow_online:
        raise RuntimeError(
            f"Required model is missing or incomplete: {repo_id}; cache={hf_home}. "
            "Populate the hf_cache volume or set HF_ALLOW_ONLINE=1 for backend-model-init."
        )

    print(f"[BackendModelPreflight] downloading: {repo_id}", flush=True)
    snapshot_download(
        repo_id=repo_id,
        cache_dir=hf_home,
        max_workers=max(1, int(os.getenv("HF_DOWNLOAD_WORKERS", "4"))),
    )
    local_path = _valid_local_model(repo_id, hf_home)
    if not local_path:
        raise RuntimeError(f"Model download completed but cache validation failed: {repo_id}")
    print(f"[BackendModelPreflight] ready: {repo_id} -> {local_path}", flush=True)
    return local_path


def main() -> int:
    hf_home = os.getenv("HF_HOME", "/root/.cache/huggingface")
    Path(hf_home).mkdir(parents=True, exist_ok=True)
    allow_online = _env_bool("HF_ALLOW_ONLINE", True)
    model_ids = {
        get_configured_embedding_model_name(),
        get_configured_rerank_model_name(),
    }
    hippo_settings = HippoRAGSettings()
    if hippo_settings.enabled:
        hippo_model = resolve_hipporag_embedding_model_name(hippo_settings)
        if any(
            marker in hippo_model.lower()
            for marker in ("contriever", "gritlm", "nv-embed-v2")
        ):
            model_ids.add(hippo_model)
    for repo_id in sorted(model_ids):
        _ensure_model(repo_id, hf_home, allow_online)
    print("[BackendModelPreflight] all required models are ready", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[BackendModelPreflight] failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)
