from __future__ import annotations

import os
import re
import json
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable

import numpy as np
from loguru import logger

from dataclasses import replace

from .file_manager import file_manager
from .retrieval.hipporag.settings import (
    HippoRAGSettings,
    versioned_hipporag_cache_root,
)
from .retrieval.hipporag.retriever import HippoRAGRetriever
from .retrieval.reranker import rerank_segment_ids
from .shared_embedding_model import encode_query_texts, get_shared_embedding_model
from .embedding_settings import get_configured_embedding_model_name
from .models import ReportContent, DocumentContent, TextSegment, ProcessingConfig
from .cross_analysis_models import (
    CrossAnalysisReport,
    CrossCompareReport,
    EvidenceRef,
    ExtractedMetric,
    CrossExtractedRecord,
)

from .cross_catalog import dimension_by_key, issue_by_key

# -------------------------
# Paths & caching
# -------------------------

UPLOADS_DIR: Path = file_manager.base_dir  # /workspace/uploads in container when mounted
EMB_DIR: Path = UPLOADS_DIR / "outputs" / "embeddings"
CROSS_CACHE_DIR: Path = UPLOADS_DIR / "outputs" / "cross_analysis"


# -------------------------
# Embedding model singleton
# -------------------------

_model = None
_model_id: Optional[str] = None


# HippoRAG / hybrid retrieval singletons
_hippo: Optional[HippoRAGRetriever] = None
_hippo_settings: Optional[HippoRAGSettings] = None
_proc_config: Optional[ProcessingConfig] = None
_report_content_cache: Dict[str, ReportContent] = {}


def set_hipporag_retriever(retriever: Optional[HippoRAGRetriever]) -> None:
    """Inject the API runtime retriever so all retrieval surfaces share it."""
    global _hippo
    _hippo = retriever


def _get_processing_config() -> ProcessingConfig:
    """Create a ProcessingConfig aligned with API startup settings (env-driven)."""
    global _proc_config
    if _proc_config is not None:
        return _proc_config

    cfg = ProcessingConfig()
    if os.getenv("LLM_API_KEY"):
        cfg.llm_api_key = os.getenv("LLM_API_KEY")
    if os.getenv("LLM_BASE_URL"):
        cfg.llm_base_url = os.getenv("LLM_BASE_URL")
    if os.getenv("LLM_MODEL"):
        cfg.llm_model = os.getenv("LLM_MODEL")

    # Align device to actual runtime availability.
    cfg.device = _get_device()
    _proc_config = cfg
    return cfg



# -------------------------
# LLM client (optional) for numeric extraction & normalization
# -------------------------

_llm_client = None


def _get_llm_client():
    """Initialize and cache an OpenAI-compatible client (DashScope compatible-mode by default)."""
    global _llm_client
    if _llm_client is not None:
        return _llm_client

    cfg = _get_processing_config()
    if not getattr(cfg, "llm_api_key", None):
        return None

    try:
        import openai  # type: ignore
    except Exception as e:
        logger.warning(f"[CrossAnalysis] openai client not available; fallback to rule extraction. err={e}")
        return None

    base_url = cfg.llm_base_url if cfg.llm_base_url else "https://dashscope.aliyuncs.com/compatible-mode/v1"
    try:
        _llm_client = openai.OpenAI(api_key=cfg.llm_api_key, base_url=base_url)
        return _llm_client
    except Exception as e:
        logger.warning(f"[CrossAnalysis] LLM client init failed; fallback to rule extraction. err={e}")
        _llm_client = None
        return None


def _default_metric_meaning(
    metric_name: str,
    unit: Optional[str],
    year: Optional[str],
    scope: Optional[str],
    issue_name: Optional[str] = None,
) -> str:
    """Short human-friendly meaning line for a numeric point.

    Goal: give readers a quick grasp of "this number means what" without long explanations.
    """
    bits = []
    if issue_name:
        bits.append(str(issue_name))
    bits.append(metric_name)
    if scope:
        bits.append(str(scope))
    if year:
        bits.append(str(year))
    if unit:
        bits.append(str(unit))
    return " · ".join([b for b in bits if b])


def _llm_extract_metrics(
    metric_name: str,
    segments: List[Tuple[Dict, float]],
    max_items: int = 18,
    issue_name: Optional[str] = None,
) -> List[ExtractedMetric]:
    """Use LLM to extract and normalize numeric points from retrieved evidence segments.

    Returns [] if LLM is not configured or fails.
    """
    enabled = os.getenv("CROSS_LLM_EXTRACT_ENABLED", "1").strip().lower() in ("1", "true", "yes", "y")
    if not enabled:
        return []

    client = _get_llm_client()
    cfg = _get_processing_config()
    if client is None:
        return []

    model = cfg.llm_model or "qwen-plus"
    # Keep context bounded: prioritize higher-score segments, but keep enough variety.
    segs = segments[: min(len(segments), 24)]
    context_lines: List[str] = []
    for seg, _s in segs:
        page = int(seg.get("page_number") or 0) or 0
        txt = _clean_snippet_for_llm(seg.get("content") or "")
        if not txt:
            continue
        context_lines.append(f"[p{page}] {txt[:900]}")
    context = "\n\n".join(context_lines)[:7000]

    # IMPORTANT: We don't expose this prompt in UI; user requested not to write prompts.
    sys = "You are a precise ESG data extraction engine. Only extract numbers explicitly stated in the text. Never invent."
    metric_title = metric_name
    if issue_name:
        metric_title = f"{issue_name} · {metric_name}"

    metric_title = metric_name if not issue_name else f"{issue_name} · {metric_name}"

    usr = f"""You are extracting structured numeric ESG metric points for Cross Analysis.
Metric: {metric_title}

From the evidence snippets below, extract up to 10 strongest numeric points that belong to THIS metric.
Pay special attention to TABLE rows (year/value/unit).

Return ONLY a JSON array. Each item must have fields:
- value: number
- unit: string or null
- year: string (YYYY) or null
- scope: string or null (e.g., Scope 1/2/3; market-based)
- confidence: number 0-1
- evidence_page: integer page number or null
- evidence_snippet: short supporting excerpt <=120 chars

Rules:
- Do NOT guess. If the number does not clearly belong to this metric, omit it.
- If units differ across snippets, keep them as-is; do not convert.
- Prefer disclosed KPI/table values over narrative examples.

Evidence:
{context}
"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            temperature=0.0,
        )
        content = (resp.choices[0].message.content or "").strip()
        # Strip fenced blocks if present
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        data = json.loads(content)
        if not isinstance(data, list):
            return []
        out: List[ExtractedMetric] = []
        for it in data[:max_items]:
            if not isinstance(it, dict):
                continue
            v = it.get("value", None)
            try:
                v = float(v) if v is not None else None
            except Exception:
                v = None
            unit = it.get("unit", None)
            year = it.get("year", None)
            scope = it.get("scope", None)
            # Keep meaning minimal for UI: indicator name only.
            meaning = metric_name
            conf = it.get("confidence", 0.55)
            try:
                conf = float(conf)
            except Exception:
                conf = 0.55

            # Evidence
            page = it.get("evidence_page", None)
            if page is None:
                page = it.get("page", None)
            try:
                page_i = int(page) if page is not None else None
            except Exception:
                page_i = None

            snip = it.get("evidence_snippet", None)
            if snip is None:
                snip = it.get("snippet", None)
            snip = _clean_snippet(str(snip or ""))[:120] if snip is not None else ""

            ev = EvidenceRef(page=page_i, position_y=None, snippet=snip, segment_id=None, reason=None)

            out.append(
                ExtractedMetric(
                    name=metric_name,
                    value=v,
                    unit=str(unit) if unit not in (None, "") else None,
                    year=str(year) if year not in (None, "") else None,
                    scope=str(scope) if scope not in (None, "") else None,
                    meaning=str(meaning) if meaning else None,
                    confidence=max(0.0, min(1.0, conf)),
                    evidence=ev,
                )
            )
        return out
    except Exception as e:
        logger.warning(f"[CrossAnalysis] LLM extract failed; fallback to rules. err={e}")
        return []


def _effective_hippo_settings() -> HippoRAGSettings:
    """Return HippoRAGSettings with runtime-safe device defaults."""
    global _hippo_settings
    if _hippo_settings is not None:
        return _hippo_settings

    settings = HippoRAGSettings()
    # Allow opt-out via env
    if os.getenv("HIPPO_ENABLED") is not None:
        enabled = os.getenv("HIPPO_ENABLED", "1").strip().lower() in ("1", "true", "yes", "y")
        settings = replace(settings, enabled=enabled)

    settings = replace(
        settings,
        cache_root=versioned_hipporag_cache_root(settings),
    )

    # Reranker device safety: default settings uses cuda:0, but many envs are CPU-only.
    dev = getattr(settings, "rerank_device", "cpu")
    if dev.startswith("cuda"):
        try:
            import torch  # type: ignore

            if not torch.cuda.is_available():
                settings = replace(settings, rerank_device="cpu")
        except Exception:
            settings = replace(settings, rerank_device="cpu")

    _hippo_settings = settings
    return settings


def _get_hippo() -> Optional[HippoRAGRetriever]:
    """Singleton HippoRAG retriever. Returns None if not available."""
    global _hippo
    if _hippo is not None:
        return _hippo

    settings = _effective_hippo_settings()
    if not settings.enabled:
        _hippo = None
        return None

    try:
        _hippo = HippoRAGRetriever(config=_get_processing_config(), settings=settings)
        return _hippo
    except Exception as e:
        logger.warning(f"[CrossAnalysis] HippoRAG init failed, will fallback to vector retrieval. err={e}")
        _hippo = None
        return None


@dataclass(frozen=True)
class _CrossRerankResult:
    idx: int
    score: float


def rerank(
    *,
    query: str,
    snippets: List[str],
    top_k: int,
) -> List[_CrossRerankResult]:
    """Adapt the shared segment reranker to Cross Analysis snippet indices."""
    if not snippets or top_k <= 0:
        return []
    settings = replace(
        _effective_hippo_settings(),
        rerank_top_k=min(len(snippets), max(1, int(top_k))),
    )
    scored = [(str(index), 0.0) for index in range(len(snippets))]
    ranked = rerank_segment_ids(
        query=query,
        scored=scored,
        get_passage=lambda raw_index: snippets[int(raw_index)],
        settings=settings,
    )
    return [
        _CrossRerankResult(idx=int(raw_index), score=float(score))
        for raw_index, score in ranked[:top_k]
    ]


def _get_report_content(file_id: str) -> ReportContent:
    """Build ReportContent from embeddings artifacts to feed HippoRAG."""
    if file_id in _report_content_cache:
        return _report_content_cache[file_id]

    art = load_artifacts(file_id)
    seg_models: List[TextSegment] = []
    # Keep ordering stable: use the embeddings segment_ids list order.
    for sid in art.segment_ids:
        seg = art.segment_by_id.get(sid)
        if not seg:
            continue
        content = str(seg.get("content") or "")
        if not content.strip():
            continue
        page_number = int(seg.get("page_number") or 0) or 1
        position_y = float(seg.get("position_y") or 0.0)
        seg_models.append(TextSegment(segment_id=str(sid), content=content, page_number=page_number, position_y=position_y))

    doc = DocumentContent(
        document_id=str(file_id),
        file_path=str((UPLOADS_DIR / "files" / f"{file_id}.pdf")),
        segments=seg_models,
        markdown_content="\n\n".join([s.content for s in seg_models[:800]]),
    )
    rc = ReportContent(document_id=str(file_id), document_content=doc, embeddings=[])
    _report_content_cache[file_id] = rc
    return rc


def _get_device() -> str:
    # Prefer explicit device env, else cuda if available
    d = os.getenv("LOCAL_EMBEDDINGS_DEVICE", "").strip().lower()
    if d:
        return d
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def get_embedding_model():
    global _model, _model_id
    if _model is not None:
        return _model

    repo_id = get_configured_embedding_model_name()
    cache_folder = os.getenv("HF_HOME", "/root/.cache/huggingface")
    device = _get_device()

    _model = get_shared_embedding_model(
        repo_id,
        device=device,
        hf_home=cache_folder,
        trust_remote_code=True,
    )
    _model_id = explicit_path or repo_id
    return _model


# -------------------------
# File artifacts
# -------------------------

@dataclass
class ReportArtifacts:
    file_id: str
    segments: List[Dict]
    segment_by_id: Dict[str, Dict]
    segment_ids: List[str]
    embeddings: np.ndarray  # normalized float32 (N,D)


_artifact_cache: Dict[str, ReportArtifacts] = {}


def load_artifacts(file_id: str) -> ReportArtifacts:
    if file_id in _artifact_cache:
        return _artifact_cache[file_id]

    seg_path = EMB_DIR / f"{file_id}_segments.json"
    emb_path = EMB_DIR / f"{file_id}_embeddings.npz"

    if not seg_path.exists() or not emb_path.exists():
        raise FileNotFoundError(f"Missing embeddings artifacts for {file_id}. Expected {seg_path} and {emb_path}")

    segments = json.loads(seg_path.read_text(encoding="utf-8"))
    segment_by_id = {s.get("segment_id"): s for s in segments if s.get("segment_id")}

    npz = np.load(str(emb_path), allow_pickle=True)
    emb = np.asarray(npz["embeddings"], dtype=np.float32)
    seg_ids = [str(x) for x in npz["segment_ids"].tolist()]

    # Normalize for cosine similarity via dot product
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    emb_norm = emb / norms

    art = ReportArtifacts(file_id=file_id, segments=segments, segment_by_id=segment_by_id, segment_ids=seg_ids, embeddings=emb_norm)
    _artifact_cache[file_id] = art
    return art


# -------------------------
# Company name heuristics
# -------------------------

_CORP_SUFFIX = re.compile(
    r"\b(Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|PLC|GmbH|S\.A\.|S\.p\.A\.|Co\.?,?\s*Ltd\.?|Company)\b",
    re.IGNORECASE,
)
_CN_SUFFIX = re.compile(r"(有限公司|集团|股份有限公司|公司)\b")


_YEAR4 = re.compile(r"\b(20\d{2})\b")


def extract_report_year(file_id: str, filename: str) -> Optional[int]:
    """Best-effort extract the report year from early pages; fallback to filename."""
    # 1) filename
    try:
        stem = Path(filename).stem
        ys = [int(y) for y in _YEAR4.findall(stem) if 2000 <= int(y) <= 2035]
        if ys:
            return max(ys)
    except Exception:
        pass

    # 2) first pages text
    try:
        seg_path = EMB_DIR / f"{file_id}_segments.json"
        if not seg_path.exists():
            return None
        segments = json.loads(seg_path.read_text(encoding="utf-8"))
        pool = [s for s in segments if int(s.get("page_number") or 0) in (1, 2, 3)]

        # Score years that appear near 'report'/'年度'/'FY'
        scores: Dict[int, float] = {}
        for s in pool[:220]:
            tt = " ".join(str(s.get("content") or "").split())
            if len(tt) < 6:
                continue
            for y in _YEAR4.findall(tt):
                yi = int(y)
                if yi < 2000 or yi > 2035:
                    continue
                w = 1.0
                if int(s.get("page_number") or 0) == 1:
                    w += 0.6
                if re.search(r"\b(FY|FISCAL|ANNUAL)\b|年度|年报|报告", tt, re.IGNORECASE):
                    w += 0.8
                scores[yi] = scores.get(yi, 0.0) + w

        if scores:
            best = sorted(scores.items(), key=lambda x: (x[1], x[0]), reverse=True)[0][0]
            return int(best)
    except Exception:
        return None

    return None


def _title_from_filename(filename: str) -> str:
    name = Path(filename).stem
    name = re.sub(r"[_\-]+", " ", name)
    # remove common ESG report tokens
    name = re.sub(r"\b(esg|sustainability|report|fy\d{2,4}|fy|annual|integrated|202\d|203\d)\b", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return Path(filename).stem
    return name.title()


def extract_company_name(file_id: str, filename: str) -> Tuple[str, str, float]:
    """Extract company legal entity name from report content.

    Requirements:
    - MUST NOT guess from filename.
    - Prefer cover/title/company profile pages.

    Returns (display_name, short_name, confidence).
    """
    unknown = ("未识别公司主体", "未识别公司主体", 0.12)
    try:
        seg_path = EMB_DIR / f"{file_id}_segments.json"
        if not seg_path.exists():
            return unknown

        segments = json.loads(seg_path.read_text(encoding="utf-8"))

        def _score_line(tt: str) -> float:
            score = 0.0
            if _CORP_SUFFIX.search(tt):
                score += 0.65
            if _CN_SUFFIX.search(tt):
                score += 0.65
            # Prefer short brand-like lines
            if 1 <= len(tt.split()) <= 10:
                score += 0.15
            # Penalize boilerplate/report tokens
            if re.search(r"(report|sustainability|environmental|social|governance|esg|csr)|报告|可持续|社会责任", tt, re.IGNORECASE):
                score -= 0.15
            # Penalize very long lines
            if len(tt) > 120:
                score -= 0.15
            return score

        # 1) First pass: cover/title pages (1-3)
        pool = [s for s in segments if int(s.get("page_number") or 0) in (1, 2, 3)]
        texts = [str(s.get("content") or "") for s in pool][:200]

        best = ""
        best_score = -1e9
        for t in texts:
            tt = " ".join(str(t).split())
            if len(tt) < 4:
                continue
            # find likely entity substrings
            cand = tt
            # If line contains both company suffix and other words, try to extract a tighter span
            m = re.search(r"([0-9A-Za-z一-鿿&.,\-\s]{2,80}(?:集团|控股|股份|有限公司|有限责任公司|Inc\.?|Ltd\.?|Corporation|Corp\.?|Holdings?))", tt)
            if m:
                cand = m.group(1)
            cand = _clean_company_name(cand)
            if not cand or len(cand) < 4:
                continue
            sc = _score_line(cand)
            if sc > best_score:
                best_score = sc
                best = cand

        if best and best_score >= 0.35:
            short = best.split(" ")[0][:28]
            return best, short, min(0.95, max(0.55, best_score))

        # 2) Fallback: semantic retrieval for "Company Name" pages, then re-score
        try:
            q = [
                "公司名称", "Company Name", "集团", "关于我们", "Company Profile", "董事长致辞", "企业简介", "公司简介",
                "We are", "About us", "Our company",
            ]
            segs = topn_segments(file_id, query_pack=q, top_n=80)
            for seg, _s in segs[:80]:
                tt = " ".join(str(seg.get("content") or "").split())
                if len(tt) < 4:
                    continue
                m = re.search(r"([0-9A-Za-z一-鿿&.,\-\s]{2,80}(?:集团|控股|股份|有限公司|有限责任公司|Inc\.?|Ltd\.?|Corporation|Corp\.?|Holdings?))", tt)
                cand = _clean_company_name(m.group(1) if m else tt)
                if not cand or len(cand) < 4:
                    continue
                sc = _score_line(cand) + 0.08
                if sc > best_score:
                    best_score = sc
                    best = cand
        except Exception:
            pass

        if best and best_score >= 0.32:
            short = best.split(" ")[0][:28]
            return best, short, min(0.9, max(0.45, best_score))

        return unknown

    except Exception:
        return unknown


# -------------------------
# Semantic retrieval + extraction
# -------------------------

def embed_query_pack(query_pack: List[str]) -> np.ndarray:
    model = get_embedding_model()
    q = [q.strip() for q in query_pack if q and q.strip()]
    if not q:
        q = ["ESG disclosure"]
    vecs = encode_query_texts(model, q, model_name_or_path=_model_id, show_progress_bar=False, normalize_embeddings=True)
    vec = np.mean(vecs, axis=0).astype(np.float32)
    # already normalized, but normalize again defensively
    n = np.linalg.norm(vec)
    if n == 0:
        return vec
    return vec / n


# -------------------------
# Fine-grained retrieval helpers (multi-query + keyword recall)
# -------------------------

_STOPWORDS_EN = set([
    'the','a','an','and','or','of','to','in','on','for','by','with','as','at','from','this','that','these','those',
    'report','reports','sustainability','esg','csr','environment','social','governance','annual','integrated','section',
])


def _env_flag(name: str, default: str = '1') -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() in ('1','true','yes','y','on')


def embed_query_pack_multi(query_pack: List[str], max_queries: int = 16) -> np.ndarray:
    """Encode a query pack into multiple normalized vectors (Q,D)."""
    model = get_embedding_model()
    q = [str(x).strip() for x in (query_pack or []) if x and str(x).strip()]
    if not q:
        q = ['ESG disclosure']

    # keep shorter, higher-signal phrases first
    q2 = []
    seen = set()
    for it in q:
        k = re.sub(r"\s+", " ", it.lower()).strip()
        if not k or k in seen:
            continue
        seen.add(k)
        q2.append(it)
        if len(q2) >= max_queries:
            break

    vecs = encode_query_texts(model, q2, model_name_or_path=_model_id, show_progress_bar=False, normalize_embeddings=True)
    return np.asarray(vecs, dtype=np.float32)


def _tokenize_keywords(query_pack: List[str], max_terms: int = 48) -> List[str]:
    """Extract conservative keyword terms from query pack for lexical recall."""
    terms: List[str] = []
    seen = set()
    for q in (query_pack or []):
        t = re.sub(r"[^0-9A-Za-z一-鿿%_/\-\s]", " ", str(q or '')).strip().lower()
        t = re.sub(r"\s{2,}", " ", t)
        if not t:
            continue
        # keep short phrases
        if 3 <= len(t) <= 32 and any(ch.isalpha() for ch in t):
            if t not in seen and t not in _STOPWORDS_EN:
                seen.add(t)
                terms.append(t)
        for tok in t.split():
            if tok in _STOPWORDS_EN:
                continue
            if len(tok) >= 3 or re.search(r"[一-鿿]", tok):
                if tok not in seen:
                    seen.add(tok)
                    terms.append(tok)
            if len(terms) >= max_terms:
                break
        if len(terms) >= max_terms:
            break

    # ensure some high-signal tokens exist
    for must in ['scope', 'trir', 'ltifr', 'tco2e', 'co2e', 'mwh', 'gj', 'm3', 'm³', 'recall', 'breach', 'penalty', 'fine']:
        if must not in seen:
            seen.add(must)
            terms.append(must)
        if len(terms) >= max_terms:
            break

    return terms[:max_terms]


def _keyword_recall(art: 'ReportArtifacts', query_pack: List[str], top_n: int = 140) -> Tuple[List[str], Dict[str, float]]:
    """Lightweight lexical recall; returns ranked segment_ids and normalized scores in [0,1]."""
    if not _env_flag('CROSS_KEYWORD_RECALL_ENABLED', '1'):
        return [], {}

    terms = _tokenize_keywords(query_pack)
    if not terms:
        return [], {}

    pats = []
    for t in terms:
        if re.search(r"[一-鿿]", t):
            pats.append((t, None, 1.0))
        else:
            if re.fullmatch(r"[a-z]{3,}", t):
                pats.append((t, re.compile(r"\\b" + re.escape(t) + r"\\b", re.I), 1.0))
            else:
                pats.append((t, None, 1.0))

    scored: List[Tuple[str, float]] = []
    for sid in art.segment_ids:
        seg = art.segment_by_id.get(sid)
        if not seg:
            continue
        txt = str(seg.get('content') or '')
        if not txt:
            continue
        tl = txt.lower()
        score = 0.0
        for term, rx, w in pats:
            if rx is not None:
                if rx.search(tl):
                    score += w
            else:
                if term in tl:
                    score += w
        if score <= 0:
            continue
        # bonus for explicit year/unit patterns
        if re.search(r"\\b20\\d{2}\\b", tl):
            score += 0.6
        if re.search(r"(?:tco2e|co2e|mwh|gj|m3|m³|%|usd|rmb|cny|hkd|sgd)", tl):
            score += 0.4
        scored.append((sid, score))

    if not scored:
        return [], {}
    scored.sort(key=lambda x: x[1], reverse=True)
    scored = scored[:max(1, int(top_n))]

    mx = scored[0][1]
    mn = scored[-1][1]
    denom = (mx - mn) if mx != mn else 1.0
    score01 = {sid: float((sc - mn) / denom) for sid, sc in scored}
    ranked = [sid for sid, _ in scored]
    return ranked, score01


def _compute_vector_sims(art: 'ReportArtifacts', query_pack: List[str]) -> np.ndarray:
    """Compute pooled similarity scores for each segment (N,)."""
    if not _env_flag('CROSS_MULTI_VECTOR_ENABLED', '1'):
        q = embed_query_pack(query_pack)
        sims = art.embeddings @ q
        return np.asarray(sims, dtype=np.float32)

    max_q = int(os.getenv('CROSS_MULTI_VECTOR_MAX_QUERIES', '16') or '16')
    qvecs = embed_query_pack_multi(query_pack, max_queries=max_q)
    sims_mat = art.embeddings @ qvecs.T
    max_sims = sims_mat.max(axis=1)
    mean_sims = sims_mat.mean(axis=1)
    sims = 0.78 * max_sims + 0.22 * mean_sims
    return np.asarray(sims, dtype=np.float32)


_REWRITE_CACHE_MAX = 256
_rewrite_cache = None

def _rewrite_query_keywords_llm(query: str) -> str:
    """Rewrite a long query into short keyword-style query (LLM, cached).

    This mirrors the chatbox HippoRAG rewrite strategy and is only used
    when HippoRAG returns empty results.
    """
    global _rewrite_cache
    q = (query or '').strip()
    if not q:
        return q

    client = _get_llm_client()
    cfg = _get_processing_config()
    if client is None or not (cfg.llm_model or '').strip():
        return q

    from collections import OrderedDict
    if _rewrite_cache is None or not isinstance(_rewrite_cache, OrderedDict):
        _rewrite_cache = OrderedDict()

    if q in _rewrite_cache:
        _rewrite_cache.move_to_end(q)
        return _rewrite_cache[q]

    model = cfg.llm_model
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    'role': 'system',
                    'content': (
                        'Rewrite the query into 5-12 concise keywords for ESG report retrieval. '
                        'Return ONLY keywords separated by single spaces. No punctuation. No explanations.'
                    ),
                },
                {'role': 'user', 'content': q},
            ],
            temperature=0.0,
            max_tokens=64,
        )
        out = (resp.choices[0].message.content or '').strip()
        out = re.sub(r"[^0-9A-Za-z一-鿿\s]", " ", out)
        out = re.sub(r"\s{2,}", " ", out).strip()
        if not out or len(out) < 3:
            out = q
    except Exception:
        out = q

    _rewrite_cache[q] = out
    _rewrite_cache.move_to_end(q)
    while len(_rewrite_cache) > _REWRITE_CACHE_MAX:
        _rewrite_cache.popitem(last=False)
    return out


def topn_segments(
    file_id: str,
    query_pack: List[str],
    query_text: Optional[str] = None,
    top_n: int = 420,
    query_pack_vec: Optional[List[str]] = None,
) -> List[Tuple[Dict, float]]:
    """Retrieve top-N candidate segments for a report.

    Pipeline:
    1) Vector recall using *English-first* embedding queries (multi-query pooling when enabled)
    2) Keyword recall (lightweight lexical) to catch token-heavy indicators
    3) Pre-rerank to filter noise
    4) HippoRAG expansion (optional) with a strict gate to prevent recall pollution
    5) Final rerank + fusion
    """
    art = load_artifacts(file_id)

    # Vector similarity for all segments
    qvec_pack = query_pack_vec or query_pack
    sims_all = _compute_vector_sims(art, qvec_pack)

    # Vector candidates
    top_n_vec = int(os.getenv('CROSS_VEC_TOPN', str(top_n)) or str(top_n))
    idx_ranked = np.argsort(-sims_all)[: min(len(sims_all), top_n_vec)]
    vec_ranked_ids = [art.segment_ids[int(i)] for i in idx_ranked]

    # Vector score map (clamped)
    id_to_idx = {sid: i for i, sid in enumerate(art.segment_ids)}
    vector_score: Dict[str, float] = {}
    for sid in vec_ranked_ids:
        i = id_to_idx.get(sid)
        if i is None:
            continue
        vector_score[sid] = max(0.0, float(sims_all[int(i)]))

    # Keyword recall
    kw_topn = int(os.getenv('CROSS_KEYWORD_TOPN', '140') or '140')
    kw_ranked_ids, kw_score01 = _keyword_recall(art, query_pack, top_n=kw_topn)

    # Union candidates
    union_max = int(os.getenv('CROSS_RECALL_UNION_MAX', str(top_n + 200)) or str(top_n + 200))
    alpha = float(os.getenv('CROSS_FUSE_KEYWORD_ALPHA', '0.10') or '0.10')
    cand_ids: List[str] = []
    seen = set()
    for sid in vec_ranked_ids:
        if sid in seen:
            continue
        seen.add(sid)
        cand_ids.append(sid)
        if len(cand_ids) >= union_max:
            break
    for sid in kw_ranked_ids:
        if sid in seen:
            continue
        seen.add(sid)
        cand_ids.append(sid)
        if len(cand_ids) >= union_max:
            break

    # Recall fusion score (vector + keyword)
    recall_score: Dict[str, float] = {}
    for sid in cand_ids:
        v = vector_score.get(sid)
        if v is None:
            i = id_to_idx.get(sid)
            if i is not None:
                v = max(0.0, float(sims_all[int(i)]))
            else:
                v = 0.0
            vector_score[sid] = v
        k = float(kw_score01.get(sid, 0.0))
        recall_score[sid] = (1.0 - alpha) * float(v) + alpha * k

    # Build initial candidates (segment dict + score)
    scored0: List[Tuple[Dict, float]] = []
    for sid in cand_ids:
        seg = art.segment_by_id.get(sid)
        if not seg:
            continue
        scored0.append((seg, recall_score.get(sid, 0.0)))

    scored0.sort(key=lambda x: x[1], reverse=True)

    # Pre-rerank: keep only high-signal candidates
    pre_keep = int(os.getenv('CROSS_PRE_RERANK_KEEP', '90') or '90')
    pre_pool = scored0[: min(len(scored0), max(30, pre_keep * 2))]

    qtext = query_text or ' ; '.join(query_pack[:24])
    try:
        rr = rerank(
            query=qtext,
            snippets=[(s.get('content') or '') for s, _ in pre_pool],
            top_k=min(len(pre_pool), max(40, pre_keep)),
        )
        rr_map = {r.idx: float(r.score) for r in rr}
        pre_pool_scored = []
        for i, (seg, base) in enumerate(pre_pool):
            r = rr_map.get(i, 0.0)
            fused = 0.72 * r + 0.28 * float(base)
            pre_pool_scored.append((seg, fused))
        pre_pool_scored.sort(key=lambda x: x[1], reverse=True)
        pre_candidates = pre_pool_scored[: min(len(pre_pool_scored), pre_keep)]
    except Exception:
        pre_candidates = pre_pool[: min(len(pre_pool), pre_keep)]

    # HippoRAG expansion (optional)
    hippo_ids: List[str] = []
    # NOTE: HippoRAG retriever is lazily created via the internal helper.
    # The previous implementation called an undefined symbol `get_hippo`,
    # which caused a 500 when cache miss triggered extraction.
    hippo = _get_hippo()
    if hippo is not None and _env_flag('CROSS_HIPPO_ENABLED', '1'):
        try:
            report_content = _get_report_content(file_id)
            top_h = int(os.getenv('CROSS_HIPPO_TOPN', '140') or '140')
            hippo_ids = hippo.retrieve_segment_ids(
                file_id,
                report_content,
                query=qtext,
                top_k=top_h,
            ) or []
        except Exception as exc:
            logger.warning(
                f"[CrossAnalysis] HippoRAG retrieval failed for {file_id}: {exc}"
            )
            hippo_ids = []

    # Add Hippo results with strict gate to reduce pollution
    min_vec_for_hippo = float(os.getenv('CROSS_HIPPO_MIN_VEC', '0.08') or '0.08')
    min_kw_for_hippo = float(os.getenv('CROSS_HIPPO_MIN_KW', '0.12') or '0.12')

    merged_ids: List[str] = []
    merged_seen = set()
    for seg, _ in pre_candidates:
        sid = str(seg.get('segment_id'))
        if not sid or sid in merged_seen:
            continue
        merged_seen.add(sid)
        merged_ids.append(sid)

    for sid in hippo_ids:
        if sid in merged_seen:
            continue
        i = id_to_idx.get(str(sid))
        v = max(0.0, float(sims_all[int(i)])) if i is not None else 0.0
        k = float(kw_score01.get(str(sid), 0.0))
        if v < min_vec_for_hippo and k < min_kw_for_hippo:
            continue
        merged_seen.add(str(sid))
        merged_ids.append(str(sid))
        if len(merged_ids) >= int(os.getenv('CROSS_FINAL_CAND_MAX', '220') or '220'):
            break

    # Final rerank on merged candidates
    final_pool: List[Tuple[Dict, float]] = []
    for sid in merged_ids:
        seg = art.segment_by_id.get(sid)
        if not seg:
            continue
        base = recall_score.get(sid)
        if base is None:
            i = id_to_idx.get(sid)
            base = max(0.0, float(sims_all[int(i)])) if i is not None else 0.0
        # small boost for hippo-only retrieved segments
        if sid in set(map(str, hippo_ids)):
            base = float(base) + float(os.getenv('CROSS_HIPPO_BOOST', '0.04') or '0.04')
        final_pool.append((seg, float(base)))

    final_pool.sort(key=lambda x: x[1], reverse=True)
    final_pool = final_pool[: min(len(final_pool), int(os.getenv('CROSS_FINAL_RERANK_POOL', '180') or '180'))]

    try:
        rr2 = rerank(
            query=qtext,
            snippets=[(s.get('content') or '') for s, _ in final_pool],
            top_k=min(len(final_pool), int(os.getenv('CROSS_FINAL_RERANK_TOPK', '120') or '120')),
        )
        rr2_map = {r.idx: float(r.score) for r in rr2}
        fused2: List[Tuple[Dict, float]] = []
        for i, (seg, base) in enumerate(final_pool):
            r = rr2_map.get(i, 0.0)
            fused = 0.74 * r + 0.26 * float(base)
            fused2.append((seg, fused))
        fused2.sort(key=lambda x: x[1], reverse=True)
        out = fused2[: min(len(fused2), top_n)]
    except Exception:
        out = final_pool[: min(len(final_pool), top_n)]

    return out

_NUM = r"(?:\d{1,3}(?:[,\s]\d{3})+|\d+)(?:\.\d+)?"

# Common physical units we trust for structured extraction.
_UNIT_PHYSICAL = r"(?:tCO2e|kgCO2e|CO2e|ktCO2e|MWh|kWh|GJ|m3|m³|L|tons?|tonnes?|kg|t)"

# Money units (best-effort; used only when metric intent is money-like)
_UNIT_MONEY = r"(?:RMB|CNY|HKD|SGD|USD|EUR|US\$|HK\$|S\$|\$|人民币|元|万元|亿元|million|billion)"

_NUM_UNIT = re.compile(rf"(?P<val>{_NUM})(?P<cnmul>万|亿|百万|千)?\s*(?P<unit>{_UNIT_PHYSICAL}|%|{_UNIT_MONEY})", re.IGNORECASE)
_PERCENT = re.compile(rf"(?P<val>{_NUM})\s*%|百分之\s*(?P<val2>{_NUM})", re.IGNORECASE)
_COUNT = re.compile(rf"(?P<val>\d{{1,6}})\s*(?P<unit>起|次|件|例|人|项|个|家|points?|cases?|incidents?|events?|complaints?|recalls?)\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d{2})\b")
_SCOPE = re.compile(r"\b(scope\s*[123]|范围[一二三])\b", re.IGNORECASE)


def _parse_number(raw: str, cnmul: Optional[str] = None) -> Optional[float]:
    """Parse a numeric string with optional Chinese/English multipliers."""
    if not raw:
        return None
    s = raw.replace(",", "").replace(" ", "").strip()
    mul = 1.0

    # Chinese multipliers
    # IMPORTANT: check longer tokens first
    if cnmul:
        cm = cnmul.strip()
        if cm == "百万":
            mul = 1_000_000.0
        elif cm == "亿":
            mul = 100_000_000.0
        elif cm == "万":
            mul = 10_000.0
        elif cm == "千":
            mul = 1_000.0

    # English multipliers in the same token (rare)
    low = s.lower()
    if low.endswith("million"):
        mul *= 1_000_000.0
        s = s[: -len("million")]
    elif low.endswith("billion"):
        mul *= 1_000_000_000.0
        s = s[: -len("billion")]

    try:
        return float(s) * mul
    except Exception:
        return None


def _to_data_str(val) -> Optional[str]:
    """Convert model output value to a stable string without forcing integer casts."""
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        return s or None
    # Keep floats without scientific notation when reasonable
    if isinstance(val, (int,)):
        return str(val)
    if isinstance(val, (float,)):
        if np.isfinite(val) and float(val).is_integer():
            return str(int(val))
        # Use Decimal for a clean, non-scientific representation
        try:
            d = Decimal(str(val))
            s = format(d, 'f').rstrip('0').rstrip('.')
            return s or str(val)
        except Exception:
            return str(val)
    return str(val).strip() or None


def _split_unit_from_data(data_str: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """If a model puts unit into data (e.g. '25%'), split it out."""
    if not data_str:
        return None, data_str
    s = data_str.strip()
    # percent
    if s.endswith('%') and len(s) > 1:
        return '%', s[:-1].strip()
    # common currency symbols
    if s.startswith(('US$', 'HK$', 'S$', '$')):
        # keep the longer prefixes first
        for sym in ('US$', 'HK$', 'S$', '$'):
            if s.startswith(sym) and len(s) > len(sym):
                return sym, s[len(sym):].strip()
    return None, data_str


def _value_in_context(*, data_str: str, context: str) -> bool:
    """Ensure the extracted value appears in the evidence snippet (anti-hallucination)."""
    if not data_str or not context:
        return False
    ctx = context.replace(',', '').replace('，', '').replace(' ', '')
    v = str(data_str).replace(',', '').replace('，', '').replace(' ', '')
    if v and v in ctx:
        return True
    # Try numeric-normalized variants (e.g., 12.0 vs 12)
    try:
        m = re.match(rf"^\s*(?P<num>{_NUM})\s*(?P<cnmul>万|亿|百万|千)?\s*$", str(data_str))
        if m:
            fv = _parse_number(m.group('num'), m.group('cnmul'))
            if fv is not None:
                if float(fv).is_integer():
                    return str(int(fv)) in ctx
                # remove trailing zeros
                ds = format(Decimal(str(fv)), 'f').rstrip('0').rstrip('.')
                if ds and ds.replace('.', '')[:12] and ds in ctx:
                    return True
    except Exception:
        pass
    return False


def _infer_variant(*, label: str, context: str) -> Optional[str]:
    """Infer a 'detail' qualifier (口径/标签细节) from the evidence context."""
    if not context:
        return None
    c = context.lower()
    patterns = [
        (r"location\s*-?based", "location-based"),
        (r"market\s*-?based", "market-based"),
        (r"operational\s+control", "operational control"),
        (r"equity\s+share", "equity share"),
        (r"restated|re-?stated", "restated"),
        (r"excluding|excl\.?", "excluding"),
        (r"consolidated", "consolidated"),
    ]
    for pat, tag in patterns:
        if re.search(pat, c):
            return tag

    # Scope 2 often has explicit variants; if label is Scope 2 and we see hints, return them.
    if 'scope 2' in (label or '').lower() or '范围二' in (label or ''):
        if 'location' in c:
            return 'location-based'
        if 'market' in c:
            return 'market-based'
    return None


def _label_gate(*, label: str, ctx_l: str) -> bool:
    """Reduce cross-metric contamination with simple label-specific constraints."""
    lb = (label or "").lower()
    c = ctx_l or ""

    # Scope guards
    if "scope 1" in lb or "范围一" in label:
        return ("scope 1" in c) or ("范围一" in c) or ("范围1" in c)
    if "scope 2" in lb or "范围二" in label:
        return ("scope 2" in c) or ("范围二" in c) or ("范围2" in c)
    if "scope 3" in lb or "范围三" in label:
        return ("scope 3" in c) or ("范围三" in c) or ("范围3" in c)
    if "scope 1、2、3" in label or ("total" in lb and "scope" in lb):
        # total GHG should mention total + scope or 范围
        return ("total" in c or "总量" in c) and ("scope" in c or "范围" in c)

    # TRIR/LTIFR guards
    if lb.strip() == "trir":
        return "trir" in c
    if lb.strip() == "ltifr":
        return "ltifr" in c

    return True


def _quality_gate(*, kind: str, data_str: str, unit: Optional[str], context: str, year: Optional[str]) -> bool:
    """Hard quality gate for extracted values.

    This is intentionally conservative to remove low-quality / mismatched records.
    It does NOT require integer-only data.
    """
    k = (kind or "").lower().strip()
    s = (data_str or "").strip()
    if not s:
        return False
    # text metrics: rely on evidence length
    if k == "text":
        return len(context or "") >= 18

    # If year is required, the caller already enforced it. But still drop weird years.
    if year:
        if not re.fullmatch(r"20\d{2}", str(year).strip()):
            return False

    # Parse numeric if possible
    val: Optional[float] = None
    try:
        m = re.match(rf"^\s*(?P<num>{_NUM})\s*(?P<cnmul>万|亿|百万|千)?\s*$", s.replace(",", "").replace("，", ""))
        if m:
            val = _parse_number(m.group('num'), m.group('cnmul'))
    except Exception:
        val = None

    # When numeric parse fails for numeric kinds, keep only if the string contains digits
    if k in ("absolute", "intensity", "ratio", "count", "money") and val is None:
        if not re.search(r"\d", s):
            return False
        # allow non-parsable strings (e.g. 'N/A', '—') to be filtered out
        if re.fullmatch(r"(?:n/?a|na|none|--|—|\-)" , s.strip().lower()):
            return False
        # otherwise keep
        return True

    # Common sanity bounds
    if val is not None:
        if val < 0:
            return False
        if val > 1e14:  # absurdly large
            return False

    u = (unit or "").lower().strip()
    ctx = (context or "").lower()

    if k == "ratio":
        # Accept 0..100 as % or small rates, also accept 0..1 for fractions
        if val is None:
            return True
        if "%" in u or "percent" in ctx or "占比" in ctx or "比例" in ctx or "率" in ctx:
            return 0.0 <= val <= 100.0 or 0.0 <= val <= 1.0
        # unitless rate: keep within a reasonable range
        return 0.0 <= val <= 1000.0

    if k == "count":
        # counts can be non-integers in some disclosures (rates mislabeled). Keep but bound.
        if val is None:
            return True
        return 0.0 <= val <= 1e9

    if k == "money":
        # require explicit currency signal either in unit or context
        if not (re.search(r"(?:rmb|cny|hkd|sgd|usd|eur|\$|元|人民币|万元|亿元)", u) or re.search(r"(?:rmb|cny|hkd|sgd|usd|eur|\$|元|人民币|万元|亿元)", ctx)):
            return False
        return True

    if k == "intensity":
        # require denominator signal when possible
        if "/" in u:
            return True
        if re.search(r"\bper\b|/|每", ctx):
            return True
        # some reports omit unit but explain it nearby; keep leniently
        return True

    # absolute or unknown: accept if numeric sanity passes
    return True


def _infer_metric_intent(metric_name: str) -> str:
    n = (metric_name or "").lower()
    # Percent-like
    if any(k in n for k in ["%", "share", "rate", "占比", "比例", "率", "resolution_rate"]):
        return "percent"
    # Money-like
    if any(k in n for k in ["investment", "invest", "投入", "罚款", "penalt", "remediation", "impairment", "减值", "risk"]):
        return "money"
    # Emissions
    if any(k in n for k in ["ghg", "co2", "emission", "排放", "碳"]):
        return "emissions"
    # Energy
    if any(k in n for k in ["energy", "electric", "power", "fuel", "能耗", "能源", "kwh", "mwh", "gj"]):
        return "energy"
    # Water
    if any(k in n for k in ["water", "wastewater", "取水", "耗水", "废水", "m3", "m³"]):
        return "water"
    # Waste
    if any(k in n for k in ["waste", "hazard", "废弃物", "危废", "recycling"]):
        return "waste"
    # Count-like
    if any(k in n for k in ["count", "incidents", "events", "complaints", "recalls", "事故", "投诉", "召回", "泄露", "死亡", "cases"]):
        return "count"
    # Intensity-like
    if "intensity" in n or "强度" in n:
        return "intensity"
    return "generic"


def _clean_snippet(text: str) -> str:
    t = " ".join(str(text).split())
    t = t.replace("\u00a0", " ").strip()
    return t

def _clean_snippet_for_llm(text: str) -> str:
    """Cleaner for LLM context.

    Preserve newlines and some table-like spacing so year/value rows remain readable.
    """
    s = str(text or '').replace('\u00a0', ' ')
    # normalize Windows newlines
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    # collapse extreme spaces but keep some alignment
    s = re.sub(r'[ \t]{4,}', '  ', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


def _looks_table_like(text: str) -> bool:
    s = str(text or '')
    if '\n' in s and len(re.findall(r'\b20\d{2}\b', s)) >= 1 and len(re.findall(r'\d', s)) >= 10:
        return True
    if '|' in s or '\t' in s:
        return True
    if len(re.findall(r'[ \t]{2,}', s)) >= 4 and len(re.findall(r'\d', s)) >= 12:
        return True
    return False

def _is_table_like(text: str) -> bool:
    """Backward-compatible wrapper."""
    return _looks_table_like(text)



def _clean_company_name(name: str) -> str:
    """Normalize company name for UI labels.

    Goals:
    - remove useless symbols / boilerplate
    - keep legal entity suffixes when present
    - keep it short & stable
    """
    n = str(name or "").strip()
    if not n:
        return ""

    # remove common decorative symbols
    n = re.sub(r"[©®™·•★☆◆◇■□▲▼▶▷→←~`]+", " ", n)
    # remove brackets content that is clearly not part of entity name (e.g., report titles)
    n = re.sub(r"[（(\[]\s*(ESG|CSR|Sustainability|可持续|环境|社会|治理|报告|Report|年度报告|年报)[^）)\]]*[）)\]]", " ", n, flags=re.I)
    # strip common report words
    n = re.sub(r"\b(ESG|CSR|Sustainability)\b", " ", n, flags=re.I)
    n = re.sub(r"(可持续发展|可持续|社会责任|环境\s*社会\s*治理)\s*(报告|report)", " ", n, flags=re.I)
    # collapse whitespace and punctuation around
    n = re.sub(r"[\s ]+", " ", n).strip(" -_—:：,，.。;；")

    # keep only a safe charset for labels (Chinese/letters/numbers/basic separators)
    n = re.sub(r"[^0-9A-Za-z一-鿿&.,\-\s]", "", n)
    n = re.sub(r"\s{2,}", " ", n).strip()

    return n[:80]


def extract_metrics_from_text(metric_name: str, texts: List[str]) -> List[ExtractedMetric]:
    """Best-effort numeric extraction (rule-based fallback).

    Improvements:
    - table-aware row parsing for year/value alignment
    - conservative patterns only (unit-attached, percent, counts)
    """
    intent = _infer_metric_intent(metric_name)
    metrics: List[ExtractedMetric] = []
    seen = set()
    max_items = int(os.getenv("CROSS_RULE_MAX_METRICS", "12"))

    def _add(val: float, unit: str, year: Optional[str], scope: Optional[str], conf: float):
        key = (round(float(val), 6), unit, year, scope)
        if key in seen:
            return
        seen.add(key)
        metrics.append(
            ExtractedMetric(
                name=metric_name,
                value=float(val),
                unit=unit,
                year=year,
                scope=scope,
                confidence=max(0.0, min(1.0, float(conf))),
            )
        )

    def _process(tt: str):
        year_m = _YEAR.search(tt)
        scope_m = _SCOPE.search(tt)
        year = year_m.group(1) if year_m else None
        scope = scope_m.group(0) if scope_m else None

        # 1) Intent-specific patterns
        if intent == "percent":
            pm = _PERCENT.search(tt)
            if pm:
                rawv = pm.group("val") or pm.group("val2")
                v = _parse_number(rawv)
                if v is not None:
                    _add(v, "%", year, scope, 0.62 if year else 0.52)
            return

        if intent == "count":
            for cm in _COUNT.finditer(tt):
                v = _parse_number(cm.group("val"))
                if v is None:
                    continue
                if 1900 <= v <= 2100:
                    continue
                unit = str(cm.group("unit"))
                _add(v, unit, year, scope, 0.60 if year else 0.50)
            return

        # 2) Generic unit-attached extraction
        for m in _NUM_UNIT.finditer(tt):
            unit = str(m.group("unit") or "").strip()
            cnmul = m.groupdict().get("cnmul")

            # money intent: only accept money-like units
            if intent == "money":
                if not re.match(rf"^{_UNIT_MONEY}$", unit, re.IGNORECASE):
                    if unit not in ("元", "万元", "亿元", "人民币"):
                        continue

            v = _parse_number(m.group("val"), cnmul=cnmul)
            if v is None:
                continue

            # Normalize some Chinese money units into numeric scaling
            if unit in ("万元",):
                v = v * 10_000.0
                unit = "CNY"
            elif unit in ("亿元",):
                v = v * 100_000_000.0
                unit = "CNY"
            elif unit in ("人民币", "元"):
                unit = "CNY"

            conf = 0.58 if year else 0.48
            if intent in ("emissions", "energy", "water", "waste"):
                conf += 0.04
            _add(v, unit, year, scope, min(0.72, conf))

    # Iterate chunks (cap), table-aware
    for t in texts[:120]:
        raw = str(t or "").replace(" ", " ")
        candidates: List[str]
        if _is_table_like(raw):
            rows = [r.strip() for r in raw.splitlines() if r.strip()]
            candidates = rows[:12]
        else:
            candidates = [raw]

        for cand in candidates:
            tt = _clean_snippet(cand)
            if len(tt) < 8:
                continue
            _process(tt)
            if len(metrics) >= max_items:
                return metrics

    return metrics


def summarize_snippets(snippets: List[str], max_items: int = 4) -> List[str]:
    # Simple extractive summary: select diverse, short lines.
    out: List[str] = []
    seen = set()
    for s in snippets:
        t = _clean_snippet(s)
        if len(t) < 18:
            continue
        t = t[:180]
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= max_items:
            break
    # If still empty, fallback
    return out or ([_clean_snippet(snippets[0])[:180]] if snippets else [])


def build_report_summary(
    file_id: str,
    display_name: str,
    short_name: str,
    metric_name: str,
    top_segments: List[Tuple[Dict, float]],
    top_k_evidence: int,
    report_year: Optional[int] = None,
    issue_display_name: Optional[str] = None,
) -> CrossCompareReport:
    # Prepare evidence
    evidence: List[EvidenceRef] = []
    snippets: List[str] = []
    # IMPORTANT: Evidence should be rich enough to show "all relevant content" for the current topic.
    # Keep at least 3 segments, and allow caller to request more (e.g. 8-12).
    for seg, score in top_segments[: max(int(top_k_evidence), 3)]:
        page = int(seg.get("page_number") or 0) or None
        py = seg.get("position_y")
        snippet = _clean_snippet(seg.get("content") or "")[:220]
        evidence.append(
            EvidenceRef(
                page=page,
                position_y=float(py) if py is not None else None,
                snippet=snippet,
                segment_id=seg.get("segment_id"),
                reason=None,
            )
        )
        snippets.append(snippet)

    # Metrics extraction (HippoRAG定位后，优先用LLM做结构化抽取与口径归一；失败则回退规则抽取)
    metrics = _llm_extract_metrics(metric_name=metric_name, segments=top_segments, issue_name=issue_display_name)
    if not metrics:
        metrics = extract_metrics_from_text(metric_name=metric_name, texts=[seg.get("content") or "" for seg, _ in top_segments])

    if metrics:
        # Attach evidence for drilldown.
        # We don't do perfect value-to-snippet alignment yet; assign the top evidence as default.
        if evidence:
            # Attach best-matching evidence by page (keep all points traceable).
            page_map: Dict[int, EvidenceRef] = {int(e.page): e for e in evidence if e.page is not None}
            for m in metrics:
                # fill meaning if missing
                if not getattr(m, 'meaning', None):
                    m.meaning = _default_metric_meaning(metric_name, m.unit, m.year, m.scope, issue_name=issue_display_name)
                # prefer page match
                mp = None
                try:
                    mp = int(m.evidence.page) if (m.evidence and m.evidence.page is not None) else None
                except Exception:
                    mp = None
                if mp is not None and mp in page_map:
                    # preserve extracted snippet-less evidence, but replace with full snippet
                    m.evidence = page_map[mp]
                elif m.evidence is None:
                    m.evidence = evidence[0]
                else:
                    # If LLM returned a page-less/empty snippet evidence, keep it traceable via first snippet.
                    if m.evidence and (not getattr(m.evidence, 'snippet', '') or str(m.evidence.snippet).strip() == ""):
                        m.evidence = evidence[0]
        status = "ok"
        reason = None
    else:
        status = "no_structured_metrics"
        reason = "未识别到稳定可比的数值口径（可能为文字披露或单位/口径缺失）"

    # API-side interpretation: concise but not overly truncated.
    summary = summarize_snippets(snippets, max_items=min(10, max(4, int(top_k_evidence))))

    return CrossCompareReport(
        file_id=file_id,
        display_name=display_name,
        short_name=short_name,
        report_year=report_year,
        status=status,
        reason=reason,
        metrics=metrics,
        summary=summary,
        evidence=evidence,
    )


# -------------------------
# Cross Analysis (beta) issue-level records extraction
# -------------------------


def _norm_unit(u: Optional[str]) -> Optional[str]:
    if u is None:
        return None
    s = str(u).strip()
    if not s:
        return None
    s = s.replace("tCO₂e", "tCO2e").replace("CO₂", "CO2")
    s = s.replace("m³", "m3")
    s = re.sub(r"\s+", "", s)
    return s


def _contains_all(text: str, terms: List[str]) -> bool:
    if not terms:
        return True
    t = (text or "").lower()
    for term in terms:
        if not term:
            continue
        if str(term).lower() not in t:
            return False
    return True


def _contains_any(text: str, terms: List[str]) -> bool:
    if not terms:
        return False
    t = (text or "").lower()
    for term in terms:
        if not term:
            continue
        if str(term).lower() in t:
            return True
    return False


_INTENSITY_PATTERN = re.compile(
    r"(?P<val>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?P<unit>tco2e|kgco2e|ktco2e|gj|mwh|kwh|m3)\s*(?:/|per|每)\s*(?P<denom>revenue|sales|income|output|production|unit|营收|收入|产量|产出)",
    re.IGNORECASE,
)


def _extract_intensity_from_text(text: str) -> List[Tuple[float, str]]:
    out: List[Tuple[float, str]] = []
    tt = str(text or "").replace("m³", "m3")
    for m in _INTENSITY_PATTERN.finditer(tt):
        v = _parse_number(m.group("val"))
        if v is None:
            continue
        unit = (m.group("unit") or "").upper()
        denom = (m.group("denom") or "").lower()
        denom = denom.replace("营收", "revenue").replace("收入", "revenue").replace("产量", "production").replace("产出", "output")
        out.append((float(v), f"{unit}/{denom}"))
    return out


def _pick_best_year(text: str, fallback_year: Optional[int] = None) -> Optional[str]:
    m = _YEAR.search(text or "")
    if m:
        return m.group(1)
    return str(fallback_year) if fallback_year else None


def _llm_extract_issue_records(
    *,
    topic_zh: str,
    type_zh: str,
    details_spec: List[dict],
    segments: List[Tuple[Dict, float]],
    max_items: int = 80,
) -> List[dict]:
    """LLM batch extraction for one (report x issue).

    Returns list of dicts with keys: label, detail, year, data, unit, page, context.
    Returns [] when LLM is not configured or fails.
    """
    enabled = os.getenv("CROSS_LLM_EXTRACT_ENABLED", "1").strip().lower() in ("1", "true", "yes", "y")
    if not enabled:
        return []

    client = _get_llm_client()
    cfg = _get_processing_config()
    if client is None:
        return []

    model = cfg.llm_model or "qwen-plus"
    segs = segments[: min(len(segments), 18)]
    context_lines: List[str] = []
    for seg, _s in segs:
        page = int(seg.get("page_number") or 0) or 0
        txt = _clean_snippet_for_llm(seg.get("content") or "")
        if not txt:
            continue
        context_lines.append(f"[p{page}] {txt[:1000]}")
    context = "\n\n".join(context_lines)[:9000]

    sys = (
        "You are a strict ESG disclosure extraction engine. "
        "Extract ONLY data explicitly stated in the evidence. "
        "Never invent, never infer missing values. "
        "If uncertain, omit."
    )

    # Keep the spec compact, but include constraints that improve precision.
    spec_json = json.dumps(details_spec, ensure_ascii=False)

    usr = f"""Task: Extract structured ESG disclosure records for Cross Analysis.

一级导航(topic): {topic_zh}
二级导航(type): {type_zh}

You MUST only extract records that match one of the labels in the spec below.

Detail spec (JSON):
{spec_json}

Output requirements:
- Return ONLY a JSON array.
- Each item must include fields:
  label (string, must exactly match spec.label),
  detail (string or null; qualifier like location-based/market-based if present),
  year (YYYY string or null if not available),
  data (string or number; keep original magnitude; do not convert),
  unit (string or null),
  page (integer page number or null),
  context (supporting excerpt <= 240 chars)
- Prefer table KPI values.
- If multiple years are present for the same detail, output separate items per year.
- If a detail is not disclosed, output nothing for it.

Evidence:
{context}
"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            temperature=0.0,
        )
        content = (resp.choices[0].message.content or "").strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        # best-effort: extract first JSON array if model added extra text
        if not content.lstrip().startswith("["):
            mm = re.search(r"\[[\s\S]*\]", content)
            if mm:
                content = mm.group(0)

        data = json.loads(content)
        if not isinstance(data, list):
            return []

        out: List[dict] = []
        for it in data[:max_items]:
            if not isinstance(it, dict):
                continue
            label = str(it.get("label") or it.get("detail") or "").strip()
            if not label:
                continue
            out.append(
                {
                    "label": label,
                    "detail": (str(it.get("detail") or "").strip() or None),
                    "year": (str(it.get("year") or "").strip() or None),
                    "data": (it.get("data", None)),
                    "unit": (str(it.get("unit") or "").strip() or None),
                    "page": it.get("page", None),
                    "context": (str(it.get("context") or "").strip() or None),
                }
            )
        return out
    except Exception as e:
        logger.warning(f"[CrossAnalysis] LLM issue-record extract failed; fallback to rules. err={e}")
        return []


def _records_rules_fallback(
    *,
    details: List[dict],
    segments: List[Tuple[Dict, float]],
    fallback_year: Optional[int] = None,
) -> List[dict]:
    """Rule-based fallback extraction for issue records.

    The goal is to produce *some* structured output without LLM.
    It is intentionally conservative: prefer unit-attached values and explicit year.
    """
    out: List[dict] = []

    # Pre-clean and cap
    segs = segments[: min(len(segments), 40)]

    for spec in details:
        label = str(spec.get("label") or spec.get("detail") or "").strip()
        kind = str(spec.get("value_kind") or "").strip().lower()
        units_allow = [_norm_unit(x) for x in (spec.get("units_allow") or [])]
        units_allow = [x for x in units_allow if x]
        must_terms = [str(x).lower() for x in (spec.get("must_terms") or []) if x]
        negative_terms = [str(x).lower() for x in (spec.get("negative_terms") or []) if x]
        year_required = bool(spec.get("year_required", True))

        # Select candidate segments for this label
        candidates: List[Tuple[Dict, float]] = []
        for seg, score in segs:
            txt = str(seg.get("content") or "")
            if negative_terms and _contains_any(txt, negative_terms):
                continue
            # must_terms in catalog are multilingual; treat as OR
            if must_terms and not _contains_any(txt, must_terms):
                continue
            candidates.append((seg, score))
        if not candidates:
            candidates = segs

        # Prefer top 10 segments for extraction
        texts = [str(s.get("content") or "") for s, _ in candidates[:10]]

        # Text-only disclosures
        if kind == "text":
            best_seg = candidates[0][0] if candidates else None
            if not best_seg:
                continue
            page = int(best_seg.get("page_number") or 0) or None
            ctx = _clean_snippet(best_seg.get("content") or "")
            if len(ctx) < 18:
                continue
            year = _pick_best_year(ctx, fallback_year if year_required else None) if year_required else None
            out.append({"label": label, "detail": _infer_variant(label=label, context=ctx), "year": year, "data": ctx[:240], "unit": None, "page": page, "context": ctx[:240]})
            continue

        # Intensity: dedicated patterns first
        if kind == "intensity":
            found = False
            for seg, _score in candidates[:12]:
                txt = str(seg.get("content") or "")
                for v, u in _extract_intensity_from_text(txt):
                    year = _pick_best_year(txt, fallback_year)
                    if year_required and not year:
                        continue
                    page = int(seg.get("page_number") or 0) or None
                    out.append({"label": label, "detail": _infer_variant(label=label, context=txt), "year": year, "data": _to_data_str(v), "unit": _norm_unit(u), "page": page, "context": _clean_snippet(txt)[:240]})
                    found = True
                    break
                if found:
                    break
            if found:
                continue

        # Generic numeric extraction
        extracted = extract_metrics_from_text(metric_name=label, texts=texts)
        if not extracted:
            continue

        # Filter by allowed units when given
        picked: Optional[ExtractedMetric] = None
        for m in extracted:
            unit = _norm_unit(m.unit)
            if units_allow and unit and unit not in units_allow:
                continue
            # Year constraint
            year = m.year or (str(fallback_year) if fallback_year else None)
            if year_required and not year:
                continue
            picked = m
            break
        if not picked:
            continue

        # Attach evidence: choose first candidate segment containing the unit/value string
        value_str = str(picked.value) if picked.value is not None else ""
        best_seg = candidates[0][0] if candidates else None
        for seg, _score in candidates[:12]:
            txt = str(seg.get("content") or "")
            if value_str and value_str in txt:
                best_seg = seg
                break
        page = int(best_seg.get("page_number") or 0) or None if best_seg else None
        ctx = _clean_snippet(best_seg.get("content") or "")[:240] if best_seg else None
        year = picked.year or (str(fallback_year) if fallback_year else None)
        out.append(
            {
                "label": label,
                "detail": _infer_variant(label=label, context=ctx or ""),
                "year": year,
                "data": _to_data_str(picked.value) if picked.value is not None else None,
                "unit": _norm_unit(picked.unit),
                "page": page,
                "context": ctx,
            }
        )

    return out




def _llm_pick_best_record(*, label: str, candidates: List[dict]) -> Optional[int]:
    """Ask the LLM to pick the best candidate among conflicting records.

    Returns the picked index in [0..len(candidates)-1], or None.
    """
    if not _env_flag('CROSS_LLM_PICK_BEST_ENABLED', '1'):
        return None
    client = _get_llm_client()
    cfg = _get_processing_config()
    if client is None:
        return None

    model = cfg.llm_model or 'qwen-plus'
    lines = []
    for i, c in enumerate(candidates[:6]):
        lines.append(
            f"[{i}] page={c.get('page')} year={c.get('year')} unit={c.get('unit')} data={c.get('data')}\ncontext={c.get('context')}"
        )
    ctx = "\n\n".join(lines)[:6000]

    sys = (
        "You are a strict ESG disclosure judge. "
        "Pick the single best candidate that EXACTLY matches the requested indicator label and is explicitly disclosed. "
        "Prefer table KPI values. Never invent."
    )
    usr = (
        f"Indicator label: {label}\n\n"
        f"Candidates:\n{ctx}\n\n"
        "Return ONLY JSON: {\"pick\": <integer index>}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{'role': 'system', 'content': sys}, {'role': 'user', 'content': usr}],
            temperature=0.0,
        )
        content = (resp.choices[0].message.content or '').strip()
        if content.startswith('```'):
            content = re.sub(r'^```(?:json)?\s*', '', content)
            content = re.sub(r'\s*```$', '', content)
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            content = m.group(0)
        data = json.loads(content)
        pick = data.get('pick', None)
        if isinstance(pick, int) and 0 <= pick < len(candidates):
            return pick
        return None
    except Exception:
        return None


def _resolve_record_conflicts(
    records: List['CrossExtractedRecord'],
    *,
    top_segments: List[Tuple[Dict, float]],
) -> List['CrossExtractedRecord']:
    """Resolve conflicting records for the same (label, detail, year, unit).

    We keep multiple records when (detail/unit/year) differ (user requirement),
    but when they are identical and only the value differs, we pick the best one.
    """
    if not records:
        return records

    # page-level evidence score from retrieval
    page_best: Dict[int, float] = {}
    try:
        for seg, sc in top_segments:
            p = int(seg.get('page_number') or 0) or 0
            if p <= 0:
                continue
            page_best[p] = max(page_best.get(p, 0.0), float(sc))
    except Exception:
        page_best = {}

    groups: Dict[Tuple[str, str, str, str], List['CrossExtractedRecord']] = {}
    for r in records:
        key = (str(r.label or ''), str(r.detail or ''), str(r.year or ''), str(r.unit or ''))
        groups.setdefault(key, []).append(r)

    out: List['CrossExtractedRecord'] = []
    for key, items in groups.items():
        if len(items) == 1:
            out.append(items[0])
            continue

        # If values identical after normalization, keep the first with strongest evidence
        vals = [str(getattr(x, 'data', '') or '') for x in items]
        if len(set(vals)) == 1:
            items_sorted = sorted(
                items,
                key=lambda r: (page_best.get(int(getattr(r, 'page', 0) or 0), 0.0), len(str(getattr(r, 'context', '') or ''))),
                reverse=True,
            )
            out.append(items_sorted[0])
            continue

        # Score-based pick
        items_sorted = sorted(
            items,
            key=lambda r: (
                page_best.get(int(getattr(r, 'page', 0) or 0), 0.0),
                1 if (getattr(r, 'unit', None) or '').strip() else 0,
                len(str(getattr(r, 'context', '') or '')),
            ),
            reverse=True,
        )

        pick = None
        # Optional LLM tie-breaker (small groups only)
        if _env_flag('CROSS_LLM_PICK_BEST_ENABLED', '1') and len(items_sorted) <= 4:
            candidates = []
            for r in items_sorted:
                candidates.append(
                    {
                        'page': getattr(r, 'page', None),
                        'year': getattr(r, 'year', None),
                        'unit': getattr(r, 'unit', None),
                        'data': getattr(r, 'data', None),
                        'context': getattr(r, 'context', None),
                    }
                )
            pick = _llm_pick_best_record(label=key[0], candidates=candidates)

        if isinstance(pick, int) and 0 <= pick < len(items_sorted):
            out.append(items_sorted[pick])
        else:
            out.append(items_sorted[0])

    return out

def extract_records_for_issue(
    *,
    file_id: str,
    report_short_name: str,
    topic_key: str,
    issue_key: str,
    top_n_candidates: int = 420,
    top_k_evidence: int = 12,
    report_year: Optional[int] = None,
    persist_output: bool = True,
) -> List[CrossExtractedRecord]:
    """Extract issue-level records for one report.

    Output is also cached under uploads/outputs/cross_analysis/cache.
    """
    dim = dimension_by_key(topic_key)
    issue = issue_by_key(topic_key, issue_key)
    if dim is None or issue is None:
        return []

    # Cache path
    cache_dir = CROSS_CACHE_DIR / "cache" / file_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"records__{topic_key}__{issue_key}.json"

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                # Backward compatible: older caches used `detail` as the main label.
                fixed = []
                for x in cached:
                    if isinstance(x, dict) and "label" not in x and "detail" in x:
                        x = dict(x)
                        x["label"] = x.get("detail")
                        x["detail"] = None
                    fixed.append(x)
                return [CrossExtractedRecord(**x) for x in fixed]
        except Exception:
            pass

    # Build query packs (issue-level)
    # - Vector recall (embedding) SHOULD prioritize English field names (your requirement)
    # - Rerank/Hippo query text can include zh+en for robustness
    def _has_latin(s: str) -> bool:
        return bool(re.search(r"[A-Za-z]", s or ""))

    query_pack_full: List[str] = [issue.type_en, issue.type_zh]
    query_pack_vec: List[str] = [issue.type_en]
    for d in issue.details:
        query_pack_full.append(d.detail)
        query_pack_full.extend(d.aliases_en)
        query_pack_full.extend(d.aliases_zh)
        query_pack_full.extend(d.must_terms)
        query_pack_full.extend(d.units_allow)

        # embedding pack: prefer English aliases + ASCII units, avoid long zh boilerplate
        query_pack_vec.append(d.detail)
        query_pack_vec.extend([x for x in d.aliases_en if _has_latin(str(x))])
        query_pack_vec.extend([x for x in d.units_allow if _has_latin(str(x))])

    query_pack_full.extend(["2020", "2021", "2022", "2023", "2024", "2025", "FY", "年度", "year"]) 

    # De-dup while preserving order
    def _dedup_keep(seq: List[str], limit: int) -> List[str]:
        seen = set()
        out = []
        for q in seq:
            qq = str(q or "").strip()
            if not qq:
                continue
            k = qq.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(qq)
            if len(out) >= limit:
                break
        return out

    qp_full = _dedup_keep(query_pack_full, 84)
    qp_vec = _dedup_keep(query_pack_vec, 54)

    # Rerank/Hippo text: English first, then zh constraints
    qtext = " ; ".join(qp_vec[:28] + qp_full[:28])

    top_segments = topn_segments(
        file_id=file_id,
        query_pack=qp_full,
        query_pack_vec=qp_vec,
        query_text=qtext,
        top_n=top_n_candidates,
    )

    # Prepare label specs for LLM / rules
    details_spec = []
    for d in issue.details:
        details_spec.append(
            {
                "label": d.detail,
                "value_kind": d.value_kind,
                "units_allow": d.units_allow,
                "must_terms": d.must_terms,
                "negative_terms": d.negative_terms,
                "year_required": d.year_required,
                "aliases_en": d.aliases_en[:8],
                "aliases_zh": d.aliases_zh[:8],
            }
        )

    extracted = _llm_extract_issue_records(
        topic_zh=dim.topic_zh,
        type_zh=issue.type_zh,
        details_spec=details_spec,
        segments=top_segments,
    )

    if not extracted:
        extracted = _records_rules_fallback(details=details_spec, segments=top_segments, fallback_year=report_year)

    # Post-filter: enforce label membership + strong quality gates
    label_set = {d.detail for d in issue.details}
    allow_units: Dict[str, List[str]] = {d.detail: [_norm_unit(x) for x in d.units_allow if x] for d in issue.details}
    year_required_map: Dict[str, bool] = {d.detail: bool(d.year_required) for d in issue.details}
    value_kind_map: Dict[str, str] = {d.detail: str(d.value_kind or "").strip().lower() for d in issue.details}
    must_terms_map: Dict[str, List[str]] = {d.detail: [str(x).lower() for x in (d.must_terms or []) if x] for d in issue.details}
    negative_terms_map: Dict[str, List[str]] = {d.detail: [str(x).lower() for x in (d.negative_terms or []) if x] for d in issue.details}
    cleaned: List[CrossExtractedRecord] = []
    seen_keys = set()
    for it in extracted:
        try:
            # Backward compatible: older extractor returns {detail=label}
            label = str(it.get("label") or it.get("detail") or "").strip()
        except Exception:
            continue
        if label not in label_set:
            continue

        ctx = str(it.get("context") or "").strip() or ""
        ctx_l = ctx.lower()

        # Negative term hard filter
        neg = negative_terms_map.get(label) or []
        if neg and _contains_any(ctx_l, neg):
            continue

        # Must-term soft-hard filter: require at least ONE must-term when provided.
        must = must_terms_map.get(label) or []
        if must:
            if not any(m in ctx_l for m in must):
                continue

        # Label-specific gate to reduce cross-metric contamination
        if not _label_gate(label=label, ctx_l=ctx_l):
            continue

        year = str(it.get("year") or "").strip() or None
        if not year and report_year and year_required_map.get(label, True):
            year = str(report_year)
        if year_required_map.get(label, True) and not year:
            continue
        unit = _norm_unit(it.get("unit"))
        # auto-infer unit from data token when model puts "25%" into data
        data_val = it.get("data", None)
        data_str = _to_data_str(data_val)
        if not unit:
            unit_guess, data_str2 = _split_unit_from_data(data_str)
            if unit_guess:
                unit = _norm_unit(unit_guess)
                data_str = data_str2

        au = allow_units.get(label) or []
        if not unit and len(au) == 1:
            unit = au[0]
        if au and unit and unit not in au:
            # allow intensity-like normalization
            if not ("/" in unit and any(_norm_unit(x) == unit for x in au)):
                continue
        page = it.get("page", None)
        try:
            page_i = int(page) if page is not None else None
        except Exception:
            page_i = None
        if data_str is None or not str(data_str).strip():
            continue

        # Infer variant detail from context (location-based vs market-based, etc.)
        variant = str(it.get("detail") or "").strip() or None
        if not variant:
            variant = _infer_variant(label=label, context=ctx)

        # Quality gate by value kind (numeric sanity + intent matching)
        kind = value_kind_map.get(label, "")
        if not _quality_gate(kind=kind, data_str=data_str, unit=unit, context=ctx, year=year):
            continue

        # Evidence gate: require the disclosed value to appear in context (avoid LLM hallucinations)
        if ctx and not _value_in_context(data_str=data_str, context=ctx):
            continue

        key = (label, variant or "", year or "", data_str, unit or "")
        if key in seen_keys:
            continue
        seen_keys.add(key)

        cleaned.append(
            CrossExtractedRecord(
                id=file_id,
                name=report_short_name,
                topic=dim.topic_zh,
                type=issue.type_zh,
                label=label,
                detail=variant,
                page=page_i,
                data=data_str,
                year=year,
                unit=unit,
                context=(ctx[:240] if ctx else None),
            )
        )

    # Resolve conflicts where (label, detail, year, unit) are identical but values differ
    cleaned = _resolve_record_conflicts(cleaned, top_segments=top_segments)

    # Persist cache
    try:
        cache_path.write_text(json.dumps([r.model_dump() for r in cleaned], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    # Persist outputs (per-report JSON & global aggregator handled at topic-level)
    if persist_output:
        out_dir = CROSS_CACHE_DIR / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        # Update per-report JSON (merge across issues)
        out_path = out_dir / f"{file_id}.json"
        existing: List[dict] = []
        if out_path.exists():
            try:
                existing = json.loads(out_path.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        merged = existing + [r.model_dump() for r in cleaned]
        # de-dup
        uniq = []
        seen = set()
        for x in merged:
            # Keep rows when label same but (detail/unit/year) differ (your requirement).
            k = (x.get("type"), x.get("label"), x.get("detail"), x.get("year"), x.get("data"), x.get("unit"))
            if k in seen:
                continue
            seen.add(k)
            uniq.append(x)
        try:
            out_path.write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return cleaned


def extract_records_for_topic(
    *,
    file_ids: List[str],
    topic_key: str,
    issue_keys: List[str],
    top_n_candidates: int = 420,
    top_k_evidence: int = 12,
    report_labels: Optional[Dict[str, Tuple[str, str, float, Optional[int]]]] = None,
    persist_output: bool = True,
) -> List[CrossExtractedRecord]:
    """Extract records for a dimension page (topic_key) across multiple reports."""

    dim = dimension_by_key(topic_key)
    if dim is None:
        return []

    # default issues for this dimension
    if not issue_keys:
        issue_keys = [i.issue_key for i in (dim.issues or [])]

    # Resolve report labels
    if report_labels is None:
        reports = get_reports_info(file_ids)
        report_labels = {r.file_id: (r.display_name, r.short_name, r.confidence, getattr(r, "report_year", None)) for r in reports}

    all_records: List[CrossExtractedRecord] = []
    for fid in file_ids:
        _display, short_name, _conf, rep_year = report_labels.get(fid, (fid, fid, 0.0, None))
        for ik in issue_keys:
            recs = extract_records_for_issue(
                file_id=fid,
                report_short_name=short_name,
                topic_key=topic_key,
                issue_key=ik,
                top_n_candidates=top_n_candidates,
                top_k_evidence=top_k_evidence,
                report_year=rep_year,
                persist_output=persist_output,
            )
            all_records.extend(recs)

    # Write aggregated output for this request
    if persist_output:
        out_dir = CROSS_CACHE_DIR / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        all_path = out_dir / "all_records.json"
        try:
            all_path.write_text(json.dumps([r.model_dump() for r in all_records], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return all_records


def load_or_build_topic_cache(file_id: str, topic_key: str) -> Optional[Dict]:
    p = CROSS_CACHE_DIR / file_id / f"{topic_key}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_topic_cache(file_id: str, topic_key: str, payload: Dict) -> None:
    d = CROSS_CACHE_DIR / file_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{topic_key}.json"
    try:
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def compare_topic(
    file_ids: List[str],
    topic_key: str,
    query_pack: List[str],
    top_n_candidates: int,
    top_k_evidence: int,
    report_labels: Dict[str, Tuple[str, str, float, Optional[int]]],
    metric_display_name: Optional[str] = None,
    issue_display_name: Optional[str] = None,
) -> List[CrossCompareReport]:
    out: List[CrossCompareReport] = []
    metric_name = topic_key.split(".")[-1]
    # Prefer UI-provided labels for human-facing names + better extraction.
    metric_for_extraction = (metric_display_name or metric_name).strip()

    for fid in file_ids:
        display_name, short_name, conf, report_year = report_labels.get(fid, (fid, fid[:8], 0.0, None))
        # Try cached topic result
        cached = load_or_build_topic_cache(fid, topic_key)
        if cached:
            try:
                rep = CrossCompareReport(**cached)
                # backfill labels (company name + year) even for older caches
                rep.display_name = display_name
                rep.short_name = short_name
                rep.report_year = report_year
                # Backfill human-facing metric name + default meaning for older caches.
                try:
                    for m in rep.metrics or []:
                        m.name = metric_for_extraction
                        if not getattr(m, "meaning", None):
                            m.meaning = _default_metric_meaning(metric_for_extraction, m.unit, m.year, m.scope, issue_name=issue_display_name)
                except Exception:
                    pass
                out.append(rep)
                continue
            except Exception:
                pass

        try:
            top_segments = topn_segments(fid, query_pack=query_pack, top_n=top_n_candidates)
            rep = build_report_summary(
                fid,
                display_name,
                short_name,
                metric_for_extraction,
                top_segments,
                top_k_evidence=top_k_evidence,
                report_year=report_year,
                issue_display_name=issue_display_name,
            )
            save_topic_cache(fid, topic_key, rep.model_dump())
            out.append(rep)
        except Exception as e:
            logger.warning(f"[CrossAnalysis] compare failed file_id={fid} topic={topic_key}: {e}")
            out.append(CrossCompareReport(
                file_id=fid,
                display_name=display_name,
                short_name=short_name,
                status="error",
                reason=f"提取失败：{e}",
                metrics=[],
                summary=[],
                evidence=[],
            ))
    return out


def _load_compliance_manifest_ca(assessment_dir: Path, file_id: str) -> Optional[dict]:
    p = assessment_dir / f"{file_id}_compliance_manifest.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _gri_sector_slug_from_compliance_stem(
    stem: str, file_id: str, gri_topic: str
) -> Optional[str]:
    """Recover GRI sector slug from upload filename stem GRI_{sector}_{topic}_{fid}_compliance."""
    suf = f"_{file_id}_compliance"
    if not stem.endswith(suf):
        return None
    part = stem[: -len(suf)]
    if not part.startswith("GRI_"):
        return None
    inner = part[4:]
    t = (gri_topic or "").strip()
    if not t:
        return None
    topic_suffix = f"_{t}"
    if not inner.endswith(topic_suffix):
        return None
    s = inner[: -len(topic_suffix)]
    return s or None


def get_reports_info(file_ids: List[str]) -> List[CrossAnalysisReport]:
    reports: List[CrossAnalysisReport] = []
    raw_meta = file_manager.metadata.get("files", {}) if file_manager.metadata else {}
    meta = raw_meta if isinstance(raw_meta, dict) else {}

    for fid in file_ids:
        try:
            raw_info = meta.get(fid, {})
            info = raw_info if isinstance(raw_info, dict) else {}
            filename = str(info.get("original_name") or info.get("safe_filename") or fid)
            canonical = UPLOADS_DIR / "outputs" / "compliance_reports"
            legacy = Path(__file__).parent.parent.parent / "outputs"
            has_assessment = (
                (canonical / f"{fid}_compliance.json").exists()
                or (legacy / f"{fid}_compliance.json").exists()
                or any(canonical.glob(f"*{fid}*_compliance.json"))
                or (legacy.exists() and any(legacy.glob(f"*{fid}*_compliance.json")))
            )
            display, short, conf = extract_company_name(fid, filename)
            ry = extract_report_year(fid, filename)
            if ry and str(ry) not in display:
                short = f"{display} {ry}".strip()
            else:
                short = display.strip() if display else short
        except Exception as e:
            logger.warning(f"[get_reports_info] Fallback for {fid}: {e}")
            filename = fid
            display = fid[:24] + "..." if len(fid) > 24 else fid
            short = display
            conf = 0.0
            ry = None
            has_assessment = False

        try:
            entry = meta.get(fid)
            if isinstance(entry, dict):
                entry["display_name"] = display
                entry["short_name"] = short
                entry["display_confidence"] = conf
        except Exception:
            pass

        framework = None
        industry = None
        semi_industry = None
        gri_sector = None
        gri_topic = None
        info_entry = meta.get(fid) if fid in meta else None
        if isinstance(info_entry, dict):
            framework = info_entry.get("framework") or None
            industry = info_entry.get("industry") or None
            semi_industry = info_entry.get("semi_industry") or None
            gri_sector = info_entry.get("gri_sector") or None
            gri_topic = info_entry.get("gri_topic") or None

        # Fallback: read from compliance JSON when metadata is missing (e.g. for same-subindustry check).
        if has_assessment and (framework is None or semi_industry is None or (framework == "GRI" and (gri_sector is None or gri_topic is None))):
            for d in (canonical, legacy):
                if not d.exists():
                    continue
                for p in d.glob(f"*{fid}*_compliance.json"):
                    if not p.is_file():
                        continue
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if framework is None and isinstance(data.get("framework"), str):
                            framework = data["framework"].strip() or None
                        if industry is None and isinstance(data.get("industry"), str):
                            industry = data.get("industry", "").strip() or None
                        if semi_industry is None:
                            stem = p.stem
                            if stem.endswith(f"_{fid}"):
                                part = stem[: -len(fid) - 1].strip("_ ")
                                if part:
                                    semi_industry = part
                            if semi_industry is None and isinstance(data.get("semi_industry"), str):
                                semi_industry = data["semi_industry"].strip() or None
                        # GRI: legacy keys on JSON; otherwise manifest + filename stem (SASB-shaped JSON omits gri_*).
                        if framework == "GRI" and (gri_sector is None or gri_topic is None):
                            if gri_sector is None and isinstance(data.get("gri_sector"), str):
                                gri_sector = data["gri_sector"].strip() or None
                            if gri_topic is None and isinstance(data.get("gri_topic"), str):
                                gri_topic = data["gri_topic"].strip() or None
                        if framework == "GRI" and (gri_sector is None or gri_topic is None):
                            man = _load_compliance_manifest_ca(d, fid)
                            if isinstance(man, dict):
                                outs = man.get("outputs") or []
                                if gri_topic is None and outs and isinstance(outs[0], dict):
                                    sk = outs[0].get("scope_key")
                                    if isinstance(sk, str) and sk.strip():
                                        gri_topic = sk.strip()
                            if gri_topic and gri_sector is None:
                                gs = _gri_sector_slug_from_compliance_stem(
                                    p.stem, fid, gri_topic
                                )
                                if gs:
                                    gri_sector = gs
                    except Exception as e:
                        logger.debug(f"[get_reports_info] Fallback read {p}: {e}")
                    break
                else:
                    continue
                break

        reports.append(CrossAnalysisReport(
            file_id=fid,
            display_name=display,
            short_name=short,
            report_year=ry,
            confidence=float(conf),
            filename=filename,
            has_assessment=has_assessment,
            framework=framework,
            industry=industry,
            semi_industry=semi_industry,
            gri_sector=gri_sector,
            gri_topic=gri_topic,
        ))

    # best-effort persist metadata updates
    try:
        if isinstance(getattr(file_manager, "metadata", None), dict):
            file_manager.metadata["files"] = meta
            file_manager._save_metadata()
    except Exception:
        pass

    return reports
