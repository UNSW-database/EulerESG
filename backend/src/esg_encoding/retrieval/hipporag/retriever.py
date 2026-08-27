"""HippoRAG retriever wrapper for EulerESG.

Put this file at:
  backend/src/esg_encoding/retrieval/hipporag/retriever.py

Goals
- **No breaking changes**: return *segment IDs* so your existing ESGChatbot
  pipeline (IDs -> content) keeps working.
- **Fast**: cache per file_id, pack short segments into fewer docs, avoid
  reindexing via a meta fingerprint, and do background indexing optionally.
- **Safe**: if HippoRAG isn't installed or fails, we return empty results so
  the app falls back to the current keyword search.

Requires
  pip install hipporag
"""

from __future__ import annotations

import json
import inspect
import math
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from ...content_revision import document_content_revision
from ...models import ProcessingConfig, ReportContent
from .settings import (
    HippoRAGSettings,
    hipporag_package_version,
    resolve_hipporag_embedding_model_name,
)


# Segment-id markers embedded into docs during indexing.
# NOTE: HippoRAG may strip or reformat "metadata-like" lines, so we support multiple encodings.
_SEG_IDS_RE = re.compile(r"__HIPPO_SEGMENT_IDS__:\s*([^\n\r]+)", re.IGNORECASE)
_SEG_IDS_RE2 = re.compile(r"Segment IDs:\s*([^\n\r]+)", re.IGNORECASE)
# Our docs also include per-segment labels like: [<segment_id> - Page <n>]
_SEG_ID_BRACKET_RE = re.compile(r"\[\s*([^\]\s]+)\s*-\s*Page\s*[^\]]*\]", re.IGNORECASE)
# Fallback: common ID token pattern (e.g., P001_S023)
_SEG_ID_TOKEN_RE = re.compile(r"\bP\d{1,4}_S\d{1,4}\b")

_HIPPO_RUNTIME_LOCK = threading.RLock()
_INDEX_LOCKS_GUARD = threading.Lock()
_INDEX_LOCKS: Dict[str, threading.RLock] = {}


def _index_lock_for_path(path: Path) -> threading.RLock:
    key = str(Path(path).resolve())
    with _INDEX_LOCKS_GUARD:
        lock = _INDEX_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _INDEX_LOCKS[key] = lock
        return lock


def _parse_segment_ids_from_text(text: str) -> List[str]:
    """Extract segment IDs from a retrieved text blob (best-effort)."""
    if not text:
        return []
    # 1) header formats
    for rgx in (_SEG_IDS_RE, _SEG_IDS_RE2):
        m = rgx.search(text)
        if m:
            segs = [x.strip() for x in m.group(1).split(",") if x.strip()]
            if segs:
                return segs
    # 2) bracket labels
    segs = [m.group(1).strip() for m in _SEG_ID_BRACKET_RE.finditer(text)]
    if segs:
        # de-dup preserving order
        seen=set(); out=[]
        for s in segs:
            if s not in seen:
                seen.add(s); out.append(s)
        return out
    # 3) token pattern
    segs = [m.group(0) for m in _SEG_ID_TOKEN_RE.finditer(text)]
    if segs:
        seen=set(); out=[]
        for s in segs:
            if s not in seen:
                seen.add(s); out.append(s)
        return out
    return []



def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _temporary_env(overrides: Dict[str, str | None]):
    """Temporarily set environment variables (restores original values)."""

    class _Ctx:
        def __enter__(self):
            self._old: Dict[str, Optional[str]] = {}
            for k, v in overrides.items():
                self._old[k] = os.environ.get(k)
                if v is None:
                    if k in os.environ:
                        del os.environ[k]
                else:
                    os.environ[k] = v
            return self

        def __exit__(self, exc_type, exc, tb):
            for k, old in self._old.items():
                if old is None:
                    if k in os.environ:
                        del os.environ[k]
                else:
                    os.environ[k] = old
            return False

    return _Ctx()


def _safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _hash_docs(docs: List[str], settings_sig: str) -> str:
    """Fast stable fingerprint for doc list + relevant settings."""
    h = blake2b(digest_size=16)
    h.update(settings_sig.encode("utf-8"))
    for d in docs:
        b = d.encode("utf-8", errors="ignore")
        h.update(len(b).to_bytes(8, "little", signed=False))
        h.update(b)
    return h.hexdigest()


def _configuration_identity(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def _settings_signature(settings: HippoRAGSettings, config: ProcessingConfig) -> str:
    # Anything that changes the index should be in this signature.
    return json.dumps(
        {
            "integration_schema": 2,
            "hipporag_version": hipporag_package_version(),
            "embedding_model": resolve_hipporag_embedding_model_name(settings),
            "embedding_endpoint": _configuration_identity(
                settings.embedding_base_url
            ),
            "pack": settings.pack_segments,
            "target_chars": settings.target_chars_per_doc,
            "max_docs": settings.max_docs_to_index,
            "min_seg_chars": settings.min_chars_per_segment,
            "llm_model": settings.llm_model_name or config.llm_model,
            "llm_endpoint": _configuration_identity(
                settings.llm_base_url or config.llm_base_url
            ),
            "extra_kwargs": _configuration_identity(
                os.getenv("HIPPO_EXTRA_KWARGS_JSON")
            ),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _segment_text(seg) -> str:
    return (getattr(seg, "content", "") or "").strip()


def _pack_segments(report_content: ReportContent, settings: HippoRAGSettings) -> List[str]:
    """Pack many short segments into fewer medium docs.

    We embed segment IDs into a header line so we can return IDs later.
    """
    segs = getattr(report_content.document_content, "segments", []) or []
    docs: List[str] = []
    if not segs:
        return docs

    current_ids: List[str] = []
    current_pages: List[str] = []
    current_parts: List[str] = []
    current_len = 0

    def flush():
        nonlocal current_ids, current_pages, current_parts, current_len
        if not current_parts:
            return
        ids = ",".join(current_ids)
        pages = ",".join(current_pages)
        body = "\n\n".join(current_parts)
        doc = f"__HIPPO_SEGMENT_IDS__: {ids}\n__HIPPO_PAGES__: {pages}\n\n{body}".strip()
        docs.append(doc)
        current_ids, current_pages, current_parts, current_len = [], [], [], 0

    for seg in segs:
        txt = _segment_text(seg)
        if len(txt) < settings.min_chars_per_segment:
            continue
        sid = str(getattr(seg, "segment_id", ""))
        page = str(getattr(seg, "page_number", ""))

        if current_len > 0 and (current_len + len(txt) + 2) > settings.target_chars_per_doc:
            flush()

        current_ids.append(sid)
        current_pages.append(page)
        current_parts.append(f"[{sid} - Page {page}]\n{txt}")
        current_len += len(txt) + 2


    flush()

    # If we have more docs than the index budget, sample evenly across the whole report
    # to avoid indexing only the beginning of the document.
    if settings.max_docs_to_index and len(docs) > settings.max_docs_to_index:
        stride = max(1, math.ceil(len(docs) / settings.max_docs_to_index))
        sampled = docs[::stride]
        # Ensure we do not exceed the budget.
        docs = sampled[: settings.max_docs_to_index]

    return docs


def _one_segment_per_doc(report_content: ReportContent, settings: HippoRAGSettings) -> List[str]:
    segs = getattr(report_content.document_content, "segments", []) or []
    docs: List[str] = []
    for seg in segs:
        txt = _segment_text(seg)
        if len(txt) < settings.min_chars_per_segment:
            continue
        sid = str(getattr(seg, "segment_id", ""))
        page = str(getattr(seg, "page_number", ""))
        docs.append(
            f"__HIPPO_SEGMENT_IDS__: {sid}\n__HIPPO_PAGES__: {page}\n\n[{sid} - Page {page}]\n{txt}".strip()
        )
    return docs


def _extract_text_blobs(obj: Any, _seen: Optional[set[int]] = None) -> List[str]:
    """Best-effort extraction of retrieved document text.

    HippoRAG 2.x returns ``QuerySolution`` objects whose retrieved source
    documents live in ``solution.docs``.  Older/local variants may instead
    return dictionaries, tuples, or text-bearing document objects.
    """
    if obj is None:
        return []
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, bytes):
        return [obj.decode("utf-8", errors="ignore")]

    if _seen is None:
        _seen = set()
    identity = id(obj)
    if identity in _seen:
        return []
    _seen.add(identity)

    collection_fields = (
        "retrieved_docs",
        "docs",
        "documents",
        "passages",
        "contexts",
        "results",
        "retrieval_results",
    )
    text_fields = ("text", "content", "page_content")

    if isinstance(obj, dict):
        out: List[str] = []
        for field in text_fields:
            if field in obj:
                out.extend(_extract_text_blobs(obj[field], _seen))
        for field in collection_fields:
            if field in obj:
                out.extend(_extract_text_blobs(obj[field], _seen))
        if out:
            return out
        return [json.dumps(obj, ensure_ascii=False)]
    if isinstance(obj, (list, tuple, set)):
        out: List[str] = []
        for x in obj:
            out.extend(_extract_text_blobs(x, _seen))
        return out

    # QuerySolution and common document wrappers expose data as attributes.
    out: List[str] = []
    for field in text_fields:
        try:
            value = getattr(obj, field)
        except (AttributeError, TypeError, ValueError):
            continue
        out.extend(_extract_text_blobs(value, _seen))
    for field in collection_fields:
        try:
            value = getattr(obj, field)
        except (AttributeError, TypeError, ValueError):
            continue
        out.extend(_extract_text_blobs(value, _seen))
    if out:
        return out
    return [str(obj)]


@dataclass
class _IndexMeta:
    fingerprint: str
    doc_count: int
    created_at: str
    settings_sig: str


class HippoRAGRetriever:
    """Per-file HippoRAG index + retrieval."""

    def __init__(self, config: ProcessingConfig, settings: Optional[HippoRAGSettings] = None):
        self.config = config
        self.settings = settings or HippoRAGSettings()
        self._rag_cache: Dict[str, Any] = {}
        self._rag_cache_guard = threading.RLock()
        self._docmap_cache: Dict[str, Dict[str, List[str]]] = {}  # file_id -> doc_idx(str) -> [segment_ids]
        self._indexing: Dict[str, bool] = {}
        self._indexing_guard = threading.Lock()
        self._validated_revisions: Dict[str, tuple[str, int, int]] = {}
        self._stats: Dict[str, int] = {
            "hipporag_calls": 0,     # 走 HippoRAG 的次数
            "base_calls": 0,         # fallback 到 BaseRAG 的次数（由 patch 增加）
            "index_builds": 0,       # 真实发生重建索引次数
        }

    def is_enabled(self) -> bool:
        return bool(self.settings.enabled)

    def is_indexing(self, file_id: str) -> bool:
        with self._indexing_guard:
            return bool(self._indexing.get(file_id))

    def _lock_for(self, file_id: str) -> threading.RLock:
        return _index_lock_for_path(self._save_dir(file_id))

    def _save_dir(self, file_id: str) -> Path:
        return self._safe_cache_target(file_id)


    def _docmap_path(self, save_dir: Path) -> Path:
        return Path(save_dir) / "docmap.json"

    def _load_docmap(self, save_dir: Path) -> Dict[str, List[str]]:
        p = self._docmap_path(save_dir)
        try:
            if p.exists():
                with p.open("r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict):
                    # ensure list-of-str
                    out: Dict[str, List[str]] = {}
                    for k, v in obj.items():
                        if isinstance(v, list):
                            out[str(k)] = [str(x) for x in v if str(x)]
                    return out
        except Exception:
            pass
        return {}

    def _write_docmap(self, save_dir: Path, docmap: Dict[str, List[str]]) -> None:
        p = self._docmap_path(save_dir)
        try:
            with p.open("w", encoding="utf-8") as f:
                json.dump(docmap, f, ensure_ascii=False, indent=2)
        except Exception:
            # docmap is best-effort; do not fail indexing
            return

    def _collect_doc_indices(self, obj: Any) -> List[int]:
        """Best-effort extraction of doc indices/ids from HippoRAG outputs."""
        out: List[int] = []
        if obj is None:
            return out
        if isinstance(obj, int):
            return [obj]
        if isinstance(obj, str):
            # common patterns: "doc_12", "12"
            out.extend([int(m.group(1)) for m in re.finditer(r"\bdoc_(\d+)\b", obj)])
            return out
        if isinstance(obj, (list, tuple)):
            for x in obj:
                out.extend(self._collect_doc_indices(x))
            return out
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = str(k).lower()
                if lk in {"doc_id", "docids", "doc_ids", "doc_idx", "doc_index", "doc_indices",
                          "document_id", "document_ids", "document_index", "document_indices",
                          "retrieved_doc_ids", "retrieved_docs", "docs", "documents"}:
                    out.extend(self._collect_doc_indices(v))
                else:
                    out.extend(self._collect_doc_indices(v))
            return out
        # generic object: look for common attributes
        for attr in ("doc_id", "doc_ids", "doc_indices", "documents", "docs"):
            if hasattr(obj, attr):
                out.extend(self._collect_doc_indices(getattr(obj, attr)))
        return out
    def _meta_path(self, save_dir: Path) -> Path:
        return save_dir / "index_meta.json"

    def _load_meta(self, save_dir: Path) -> Optional[_IndexMeta]:
        p = self._meta_path(save_dir)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return _IndexMeta(
                fingerprint=str(data.get("fingerprint", "")),
                doc_count=int(data.get("doc_count", 0)),
                created_at=str(data.get("created_at", "")),
                settings_sig=str(data.get("settings_sig", "")),
            )
        except Exception:
            return None

    def _write_meta(self, save_dir: Path, meta: _IndexMeta) -> None:
        p = self._meta_path(save_dir)
        try:
            p.write_text(
                json.dumps(
                    {
                        "fingerprint": meta.fingerprint,
                        "doc_count": meta.doc_count,
                        "created_at": meta.created_at,
                        "settings_sig": meta.settings_sig,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[HippoRAG] write meta failed: {e}")
            raise

    def _import_hipporag(self):
        try:
            from hipporag import HippoRAG  # type: ignore
            return HippoRAG
        except Exception as e:
            raise RuntimeError("HippoRAG is not installed. Run: pip install hipporag") from e

    def _runtime_embedding_model_name(self, model_name: Optional[str] = None) -> str:
        """Prefer the preflight-validated local snapshot for HF embeddings."""
        configured = str(
            model_name or resolve_hipporag_embedding_model_name(self.settings)
        ).strip()
        if not configured:
            return "facebook/contriever"

        configured_path = Path(configured)
        if configured_path.exists():
            return str(configured_path.resolve())

        # model_preflight stores snapshots directly below HF_HOME. Transformers
        # normally searches HF_HOME/hub when given only a repository ID, so pass
        # the resolved snapshot path to HippoRAG to guarantee offline reuse.
        if not self.settings.embedding_base_url:
            try:
                from ...shared_embedding_model import prefer_local_model

                local_ref = prefer_local_model(
                    configured,
                    hf_home=os.getenv("HF_HOME", "/root/.cache/huggingface"),
                )
                if local_ref.local_path:
                    return str(Path(local_ref.local_path).resolve())
            except Exception as exc:
                logger.debug(
                    f"[HippoRAG] local embedding snapshot lookup failed for "
                    f"'{configured}': {exc}"
                )
        return configured

    def _init_rag(self, save_dir: Path):
        HippoRAG = self._import_hipporag()

        llm_model_name = self.settings.llm_model_name or self.config.llm_model
        llm_base_url = self.settings.llm_base_url or (self.config.llm_base_url or None)

        kwargs: Dict[str, Any] = {
            "save_dir": str(save_dir),
            "llm_model_name": llm_model_name,
            "embedding_model_name": self._runtime_embedding_model_name(),
        }
        # Keep HippoRAG graph embeddings on the configured non-NVIDIA fallback.
        if (
            isinstance(kwargs.get("embedding_model_name"), str)
            and "nvidia" in kwargs["embedding_model_name"].lower()
        ):
            fallback = getattr(
                self.settings,
                "fallback_embedding_model_name",
                "facebook/contriever",
            )
            logger.warning(
                "[HippoRAG] NVIDIA embedding configured; switching to the "
                f"supported fallback '{fallback}'."
            )
            kwargs["embedding_model_name"] = self._runtime_embedding_model_name(
                fallback
            )
        if llm_base_url:
            kwargs["llm_base_url"] = llm_base_url
        if self.settings.embedding_base_url:
            kwargs["embedding_base_url"] = self.settings.embedding_base_url

        # Extra HippoRAG init kwargs (optional)
        # This lets you tune HippoRAG without changing code. For example:
        #   HIPPO_EXTRA_KWARGS_JSON='{"max_phrases_per_doc": 256, "bm25_k1": 1.2}'
        extra_json = os.getenv("HIPPO_EXTRA_KWARGS_JSON", "").strip()
        if extra_json:
            try:
                extra = json.loads(extra_json)
                if isinstance(extra, dict):
                    # Avoid letting env override the essentials
                    for k in ("save_dir", "llm_model_name", "embedding_model_name"):
                        extra.pop(k, None)
                    kwargs.update(extra)
            except Exception as e:
                logger.warning(f"[HippoRAG] invalid HIPPO_EXTRA_KWARGS_JSON: {e}")

        env = self._runtime_env()
        return kwargs, env, HippoRAG

    def _runtime_env(self) -> Dict[str, Optional[str]]:
        llm_base_url = self.settings.llm_base_url or (
            self.config.llm_base_url or None
        )
        llm_api_key = self.settings.llm_api_key or (
            self.config.llm_api_key or None
        )
        return {
            "OPENAI_API_KEY": llm_api_key,
            "OPENAI_BASE_URL": llm_base_url,
            "OPENAI_API_BASE": llm_base_url,
        }

    def _create_rag(self, save_dir: Path):
        """Create one HippoRAG instance through a version-compatible path."""
        kwargs, env, HippoRAG = self._init_rag(save_dir)
        try:
            parameters = inspect.signature(HippoRAG.__init__).parameters.values()
            accepts_extra = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
            if not accepts_extra:
                supported = {
                    parameter.name
                    for parameter in parameters
                    if parameter.name != "self"
                }
                rejected = sorted(set(kwargs) - supported)
                if rejected:
                    logger.warning(
                        f"[HippoRAG] ignoring unsupported init options: {rejected}"
                    )
                    kwargs = {
                        key: value for key, value in kwargs.items() if key in supported
                    }
        except (TypeError, ValueError):
            # Some extension-backed callables do not expose a Python signature.
            pass

        with _HIPPO_RUNTIME_LOCK, _temporary_env(env):
            try:
                return HippoRAG(**kwargs)
            except TypeError as exc:
                safe_names = {
                    "save_dir",
                    "llm_model_name",
                    "llm_base_url",
                    "embedding_model_name",
                    "embedding_base_url",
                    "azure_endpoint",
                    "azure_embedding_endpoint",
                }
                retry_kwargs = {
                    key: value for key, value in kwargs.items() if key in safe_names
                }
                if retry_kwargs == kwargs:
                    raise
                logger.warning(
                    f"[HippoRAG] init options rejected ({exc}); retrying "
                    "with the version-safe option set"
                )
                return HippoRAG(**retry_kwargs)
            except (AssertionError, ValueError) as exc:
                if "unknown embedding model" not in str(exc).lower():
                    raise
                fallback = str(
                    self.settings.fallback_embedding_model_name
                    or "facebook/contriever"
                )
                logger.warning(
                    f"[HippoRAG] unsupported embedding backend "
                    f"'{kwargs.get('embedding_model_name')}'; using '{fallback}'"
                )
                retry_kwargs = dict(kwargs)
                retry_kwargs["embedding_model_name"] = (
                    self._runtime_embedding_model_name(fallback)
                )
                return HippoRAG(**retry_kwargs)

    @staticmethod
    def _meta_matches(
        meta: Optional[_IndexMeta],
        *,
        fingerprint: str,
        doc_count: int,
        settings_sig: str,
        ready_path: Path,
        force_reindex: bool,
    ) -> bool:
        return bool(
            not force_reindex
            and meta is not None
            and meta.fingerprint == fingerprint
            and meta.doc_count == doc_count
            and meta.settings_sig == settings_sig
            and ready_path.exists()
        )

    def _remember_validated_report(
        self,
        file_id: str,
        report_content: ReportContent,
    ) -> None:
        with self._rag_cache_guard:
            self._validated_revisions[file_id] = document_content_revision(
                report_content
            )

    def _report_is_validated(
        self,
        file_id: str,
        report_content: ReportContent,
    ) -> bool:
        with self._rag_cache_guard:
            if self.settings.force_reindex or file_id not in self._rag_cache:
                return False
            return self._validated_revisions.get(
                file_id
            ) == document_content_revision(report_content)

    def _get_cached_rag(self, file_id: str):
        with self._rag_cache_guard:
            rag = self._rag_cache.pop(file_id, None)
            if rag is not None:
                # Dict insertion order acts as a small LRU.
                self._rag_cache[file_id] = rag
            return rag

    def _cache_rag(self, file_id: str, rag: Any) -> None:
        with self._rag_cache_guard:
            self._rag_cache.pop(file_id, None)
            self._rag_cache[file_id] = rag
            limit = max(1, int(self.settings.max_cached_indexes))
            while len(self._rag_cache) > limit:
                oldest_file_id = next(iter(self._rag_cache))
                if oldest_file_id == file_id and len(self._rag_cache) > 1:
                    oldest_file_id = next(
                        key for key in self._rag_cache if key != file_id
                    )
                self._rag_cache.pop(oldest_file_id, None)
                self._validated_revisions.pop(oldest_file_id, None)

    def _drop_cached_rag(self, file_id: str) -> None:
        with self._rag_cache_guard:
            self._rag_cache.pop(file_id, None)
            self._validated_revisions.pop(file_id, None)

    def _safe_cache_target(self, file_id: str) -> Path:
        root = Path(self.settings.cache_root).resolve()
        target = (root / str(file_id)).resolve()
        try:
            relative = target.relative_to(root)
        except ValueError as exc:
            raise ValueError("HippoRAG file_id resolves outside the cache root") from exc
        if target == root or not relative.parts:
            raise ValueError("HippoRAG file_id must identify a cache child directory")
        return target

    def _build_docs(self, report_content: ReportContent) -> List[str]:
        if not report_content or not getattr(report_content, "document_content", None):
            return []
        if self.settings.pack_segments:
            return _pack_segments(report_content, self.settings)
        return _one_segment_per_doc(report_content, self.settings)

    def ensure_index(self, file_id: str, report_content: ReportContent) -> bool:
        """Ensure a validated, clean index exists for ``report_content``."""
        if not self.is_enabled():
            return False

        if self._report_is_validated(file_id, report_content):
            return True

        docs = self._build_docs(report_content)
        if not docs:
            return False

        save_dir = self._save_dir(file_id)
        docmap: Dict[str, List[str]] = {}
        for i, d in enumerate(docs):
            segs = _parse_segment_ids_from_text(d)
            if segs:
                docmap[str(i)] = segs

        settings_sig = _settings_signature(self.settings, self.config)
        fingerprint = _hash_docs(docs, settings_sig)
        ready_path = save_dir / ".ready"
        lock = self._lock_for(file_id)
        with lock:
            meta = self._load_meta(save_dir)
            if self._meta_matches(
                meta,
                fingerprint=fingerprint,
                doc_count=len(docs),
                settings_sig=settings_sig,
                ready_path=ready_path,
                force_reindex=self.settings.force_reindex,
            ):
                try:
                    if self._get_cached_rag(file_id) is None:
                        self._cache_rag(file_id, self._create_rag(save_dir))
                except Exception as exc:
                    logger.warning(
                        f"[HippoRAG] existing index initialization failed for "
                        f"file_id={file_id}: {exc}"
                    )
                    return False
                self._docmap_cache[file_id] = docmap
                if not self._docmap_path(save_dir).exists():
                    self._write_docmap(save_dir, docmap)
                self._remember_validated_report(file_id, report_content)
                return True

            # HippoRAG's index() is incremental. Build in a fresh directory so
            # removed or revised evidence can never survive a re-index.
            stale_dir: Optional[Path] = None
            try:
                if save_dir.exists():
                    stale_dir = save_dir.with_name(
                        f"{save_dir.name}.stale-{os.getpid()}-{time.time_ns()}"
                    )
                    save_dir.rename(stale_dir)
                _safe_mkdir(save_dir)
            except Exception:
                logger.exception(
                    f"[HippoRAG] cache preparation failed for file_id={file_id}"
                )
                try:
                    if (
                        stale_dir is not None
                        and stale_dir.exists()
                        and not save_dir.exists()
                    ):
                        stale_dir.rename(save_dir)
                except Exception as rollback_exc:
                    logger.error(
                        f"[HippoRAG] cache preparation rollback failed for "
                        f"file_id={file_id}: {rollback_exc}"
                    )
                return False
            self._drop_cached_rag(file_id)
            self._docmap_cache.pop(file_id, None)
            self._write_docmap(save_dir, docmap)

            try:
                rag = self._create_rag(save_dir)
                with _HIPPO_RUNTIME_LOCK, _temporary_env(self._runtime_env()):
                    logger.info(f"[HippoRAG] indexing file_id={file_id} docs={len(docs)}")
                    rag.index(docs)

                self._write_meta(
                    save_dir,
                    _IndexMeta(
                        fingerprint=fingerprint,
                        doc_count=len(docs),
                        created_at=_now_iso(),
                        settings_sig=settings_sig,
                    ),
                )
                ready_path.write_text(_now_iso(), encoding="utf-8")
                self._cache_rag(file_id, rag)
                self._docmap_cache[file_id] = docmap
                self._remember_validated_report(file_id, report_content)
                self._stats["index_builds"] += 1

                if stale_dir is not None and stale_dir.exists():
                    try:
                        shutil.rmtree(stale_dir)
                    except Exception as cleanup_exc:
                        logger.warning(
                            f"[HippoRAG] stale cache cleanup deferred: {cleanup_exc}"
                        )
                return True
            except Exception:
                # Keep full traceback - the typical failure here is inside HippoRAG OpenIE/NER
                # parsing (e.g., entities returned as dict -> unhashable), or LLM config.
                logger.exception(f"[HippoRAG] indexing failed (fallback to keyword). file_id={file_id}")
                self._drop_cached_rag(file_id)
                self._docmap_cache.pop(file_id, None)
                try:
                    if save_dir.exists():
                        shutil.rmtree(save_dir)
                    if stale_dir is not None and stale_dir.exists():
                        stale_dir.rename(save_dir)
                except Exception as rollback_exc:
                    logger.error(
                        f"[HippoRAG] cache rollback failed for file_id={file_id}: "
                        f"{rollback_exc}"
                    )
                return False

    def schedule_index(self, file_id: str, report_content: ReportContent) -> bool:
        """Warm-index in a background thread and report whether it was queued."""
        if not self.is_enabled() or not self.settings.warm_index_in_background:
            return False
        with self._indexing_guard:
            if self._indexing.get(file_id):
                return False
            # Set before starting the worker so duplicate schedules cannot race.
            self._indexing[file_id] = True

        def _worker():
            try:
                self.ensure_index(file_id, report_content)
            finally:
                with self._indexing_guard:
                    self._indexing[file_id] = False

        try:
            t = threading.Thread(
                target=_worker,
                daemon=True,
                name=f"hipporag-index-{file_id}",
            )
            t.start()
            return True
        except Exception:
            with self._indexing_guard:
                self._indexing[file_id] = False
            raise

    def retrieve_segment_ids(
        self,
        file_id: str,
        report_content: ReportContent,
        query: str,
        top_k: Optional[int] = None,
    ) -> List[str]:
        """Retrieve segment IDs for a query, returning ``[]`` on failure.

        ``top_k`` lets callers such as Cross Analysis request a wider candidate
        pool.  Chat callers that omit it retain the configured context limit.
        """
        if not self.is_enabled():
            return []

        default_retrieve_limit = max(1, int(self.settings.top_k_docs))
        default_result_limit = max(
            1,
            int(self.settings.max_segment_ids_for_context),
        )
        if top_k is None:
            retrieve_limit = default_retrieve_limit
            result_limit = default_result_limit
        else:
            try:
                requested_limit = max(1, int(top_k))
            except (TypeError, ValueError):
                requested_limit = default_result_limit
            candidate_cap = max(1, int(self.settings.max_union_candidates))
            result_limit = min(requested_limit, candidate_cap)
            retrieve_limit = result_limit

        with self._indexing_guard:
            indexing = bool(self._indexing.get(file_id))
        if indexing:
            return []

        try:
            if not self.ensure_index(file_id, report_content):
                return []
            save_dir = self._save_dir(file_id)
        except Exception as exc:
            logger.warning(
                f"[HippoRAG] index validation failed for file_id={file_id}: {exc}"
            )
            return []

        rag = self._get_cached_rag(file_id)
        if rag is None:
            logger.warning(
                f"[HippoRAG] validated index has no runtime instance for "
                f"file_id={file_id}"
            )
            return []

        try:
            self._stats["hipporag_calls"] += 1
            with _HIPPO_RUNTIME_LOCK, _temporary_env(self._runtime_env()):
                raw = rag.retrieve(
                    queries=[query],
                    num_to_retrieve=retrieve_limit,
                )

            texts = _extract_text_blobs(raw)

            ids: List[str] = []
            seen = set()

            # 1) Preferred: parse segment IDs directly from returned text blobs.
            for t in texts:
                for sid in _parse_segment_ids_from_text(t):
                    if sid in seen:
                        continue
                    seen.add(sid)
                    ids.append(sid)
                    if len(ids) >= result_limit:
                        return ids

            if ids:
                return ids

            # 2) If HippoRAG returns doc indices/ids rather than raw text, recover via docmap.
            docmap = self._docmap_cache.get(file_id) or {}
            if not docmap:
                docmap = self._load_docmap(save_dir)
                # If the index exists but docmap is missing (old runs), rebuild docmap from
                # current report_content without forcing a re-index.
                if not docmap:
                    try:
                        rebuilt_docs = self._build_docs(report_content)
                        tmp: Dict[str, List[str]] = {}
                        for i, d in enumerate(rebuilt_docs):
                            segs = _parse_segment_ids_from_text(d)
                            if segs:
                                tmp[str(i)] = segs
                        if tmp:
                            docmap = tmp
                            self._write_docmap(save_dir, docmap)
                    except Exception:
                        pass
                self._docmap_cache[file_id] = docmap

            for di in self._collect_doc_indices(raw):
                segs = docmap.get(str(di))
                if not segs:
                    continue
                for sid in segs:
                    if sid in seen:
                        continue
                    seen.add(sid)
                    ids.append(sid)
                    if len(ids) >= result_limit:
                        return ids

            if ids:
                return ids

            # 3) Debug breadcrumbs (kept short) -> caller will fallback.
            if isinstance(raw, dict):
                keys = list(raw.keys())[:12]
            else:
                keys = None
            preview = ""
            try:
                preview = str(raw)
            except Exception:
                preview = repr(raw)
            preview = preview[:600]
            tprev = " | ".join([(t or "")[:120].replace("\n", " ") for t in (texts or [])[:3]])
            logger.info(f"[HippoRAG] empty results after parse. raw_type={type(raw).__name__} keys={keys} texts_preview='{tprev}' raw_preview='{preview}'")
            return []
        except Exception as e:
            logger.warning(f"[HippoRAG] retrieve failed (fallback). Error: {e}")
            return []

    def get_status(self, file_id: Optional[str] = None) -> Dict[str, object]:
        enabled = self.is_enabled()
        indexing = self.is_indexing(file_id) if (file_id and enabled) else False

        last_index_time = None
        ready = False
        doc_count = 0

        if file_id and enabled:
            save_dir = self._save_dir(file_id)
            meta = self._load_meta(save_dir)
            if meta:
                last_index_time = meta.created_at or None
                doc_count = int(meta.doc_count or 0)
                # 有 meta + doc_count>0 + 当前不在 indexing => ready
                ready = (
                    doc_count > 0
                    and (save_dir / ".ready").exists()
                    and not indexing
                )

        return {
            "enabled": enabled,
            "ready": ready,
            "indexing": indexing,
            "last_index_time": last_index_time,
            "doc_count": doc_count,
            "stats": dict(self._stats),
        }
