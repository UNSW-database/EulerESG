from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

from .hipporag.settings import HippoRAGSettings
from ..embedding_settings import get_configured_rerank_model_dtype
from ..shared_embedding_model import encode_query_texts, get_shared_embedding_model, prefer_local_model

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cached_rerankers: Dict[tuple[str, str, bool, str, str], object] = {}


def _positive_env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default)) or str(default)))
    except (TypeError, ValueError):
        return max(minimum, default)


def _env_limit_or_model(
    name: str,
    default: int,
    model_limit: int,
    *,
    minimum: int = 1,
) -> int:
    raw = str(os.getenv(name, str(default)) or str(default)).strip().lower()
    if raw in {"auto", "model", "max"}:
        return max(minimum, int(model_limit))
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        return max(minimum, default)


def _truncate_for_rerank(value: object, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _torch_dtype_from_reranker_precision(torch_module, *, device: str, use_fp16: bool):
    """Map Docker-configured reranker precision to a transformers dtype."""
    dtype = get_configured_rerank_model_dtype("float16" if use_fp16 else "float32")
    if dtype == "auto":
        return "auto"
    if dtype == "float16":
        return torch_module.float16 if str(device).startswith("cuda") else torch_module.float32
    if dtype == "bfloat16":
        return getattr(torch_module, "bfloat16", torch_module.float32)
    return torch_module.float32


class _QwenReranker:
    """Qwen3 reranker wrapper using the official CausalLM scoring path.

    Keeps a FlagReranker-compatible `compute_score` interface so the rest of
    the codebase does not need to change.
    """

    _DEFAULT_INSTRUCTION = (
        "Retrieve the most relevant evidence passage from the same ESG report for metric extraction and disclosure assessment. "
        "Prefer passages that directly support the specific metric, topic, or code and can be used to judge disclosed, partially disclosed, or not disclosed. "
        "Prefer direct evidence statements over broad topic discussion. Do not prioritize passages only because they share the same unit or time period. "
        "Deprioritize generic commitments, aspirations, future targets, boilerplate, and passages that are only topically related but cannot directly support the metric or disclosure judgment."
    )
    _SYSTEM_PREFIX = (
        '<|im_start|>system\n'
        'Judge whether the Document meets the requirements based on the Query and the Instruct provided. '
        'Note that the answer can only be "yes" or "no".'
        '<|im_end|>\n<|im_start|>user\n'
    )
    _ASSISTANT_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'

    def __init__(self, model_name_or_path: str, device: str, use_fp16: bool):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        ref = prefer_local_model(model_name_or_path)
        preferred = ref.local_path or model_name_or_path

        self._torch = torch
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(preferred, padding_side="left")
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        dtype = _torch_dtype_from_reranker_precision(torch, device=device, use_fp16=use_fp16)
        model_kwargs = {"dtype": dtype} if dtype is not None else {}
        self.model = AutoModelForCausalLM.from_pretrained(
            preferred,
            **model_kwargs,
        ).to(device).eval()

        self.token_false_id = self.tokenizer.convert_tokens_to_ids("no")
        self.token_true_id = self.tokenizer.convert_tokens_to_ids("yes")
        if self.token_false_id is None or self.token_true_id is None:
            # conservative fallback
            self.token_false_id = self.tokenizer("no", add_special_tokens=False).input_ids[0]
            self.token_true_id = self.tokenizer("yes", add_special_tokens=False).input_ids[0]

        model_context_limit = getattr(self.model.config, "max_position_embeddings", None)
        try:
            model_context_limit = int(model_context_limit)
        except (TypeError, ValueError):
            model_context_limit = 0
        if model_context_limit < 512:
            tokenizer_limit = getattr(self.tokenizer, "model_max_length", 8192) or 8192
            try:
                tokenizer_limit = int(tokenizer_limit)
            except (TypeError, ValueError):
                tokenizer_limit = 8192
            model_context_limit = tokenizer_limit if 512 <= tokenizer_limit <= 1_000_000 else 8192

        configured_max_length = _env_limit_or_model(
            "LOCAL_RERANKER_MAX_LENGTH",
            2048,
            model_context_limit,
            minimum=512,
        )
        self.model_context_limit = int(model_context_limit)
        self.max_length = int(min(configured_max_length, self.model_context_limit))
        self.batch_size = _positive_env_int("LOCAL_RERANKER_BATCH_SIZE", 4)
        self.max_query_chars = _positive_env_int("LOCAL_RERANKER_MAX_QUERY_CHARS", 1600, minimum=256)
        self.max_instruction_chars = _positive_env_int(
            "LOCAL_RERANKER_MAX_INSTRUCTION_CHARS", 1200, minimum=256
        )
        self.max_passage_chars = _env_limit_or_model(
            "LOCAL_RERANKER_MAX_CHARS_PER_PASSAGE",
            1200,
            self.model_context_limit,
            minimum=256,
        )
        self.max_padded_chars_per_batch = _env_limit_or_model(
            "LOCAL_RERANKER_MAX_PADDED_CHARS_PER_BATCH",
            self.model_context_limit,
            self.model_context_limit,
            minimum=1024,
        )
        self.prefix_tokens = self.tokenizer.encode(self._SYSTEM_PREFIX, add_special_tokens=False)
        self.suffix_tokens = self.tokenizer.encode(self._ASSISTANT_SUFFIX, add_special_tokens=False)
        logger.info(
            "[Rerank] Qwen runtime batch=%s max_length=%s model_limit=%s "
            "passage_chars=%s padded_chars_per_batch=%s",
            self.batch_size,
            self.max_length,
            self.model_context_limit,
            self.max_passage_chars,
            self.max_padded_chars_per_batch,
        )

    @classmethod
    def _format_instruction(cls, instruction: str | None, query: str, doc: str) -> str:
        task_instruction = instruction or cls._DEFAULT_INSTRUCTION
        return "<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {doc}".format(
            instruction=task_instruction,
            query=query,
            doc=doc,
        )

    def _process_inputs(self, texts: Sequence[str]):
        token_budget = max(256, self.max_length - len(self.prefix_tokens) - len(self.suffix_tokens))
        inputs = self.tokenizer(
            list(texts),
            padding=False,
            truncation="longest_first",
            return_attention_mask=False,
            max_length=token_budget,
        )
        for i, ele in enumerate(inputs["input_ids"]):
            inputs["input_ids"][i] = self.prefix_tokens + ele + self.suffix_tokens
        inputs = self.tokenizer.pad(inputs, padding=True, return_tensors="pt", max_length=self.max_length)
        for key in inputs:
            inputs[key] = inputs[key].to(self.model.device)
        return inputs

    def _score_formatted_batch(self, formatted: Sequence[str]) -> List[float]:
        inputs = self._process_inputs(formatted)
        with self._torch.no_grad():
            batch_scores = self.model(**inputs).logits[:, -1, :]
            true_vector = batch_scores[:, self.token_true_id]
            false_vector = batch_scores[:, self.token_false_id]
            pair_scores = self._torch.stack([false_vector, true_vector], dim=1)
            pair_scores = self._torch.nn.functional.log_softmax(pair_scores, dim=1)
            probs = pair_scores[:, 1].exp().detach().float().cpu().tolist()
        return [float(value) for value in probs]

    def _formatted_batches(self, formatted: Sequence[str]):
        batch_size = max(1, int(getattr(self, "batch_size", 4) or 4))
        padded_char_budget = max(
            1,
            int(getattr(self, "max_padded_chars_per_batch", 2**31 - 1) or 2**31 - 1),
        )
        current: List[str] = []
        padded_width = 0
        for item in formatted:
            item_width = max(1, len(item))
            projected_width = max(padded_width, item_width)
            projected_cost = projected_width * (len(current) + 1)
            if current and (
                len(current) >= batch_size
                or projected_cost > padded_char_budget
            ):
                yield current
                current = []
                padded_width = 0
            current.append(item)
            padded_width = max(padded_width, item_width)
        if current:
            yield current

    def compute_score(self, sentence_pairs: Sequence[Sequence[str]], normalize: bool = False, instruction: str | None = None):
        if not sentence_pairs:
            return []

        safe_instruction = _truncate_for_rerank(
            instruction or self._DEFAULT_INSTRUCTION,
            getattr(self, "max_instruction_chars", 1200),
        )
        formatted: List[str] = []
        for pair in sentence_pairs:
            if len(pair) < 2:
                raise ValueError("Each rerank pair must contain [query, document]")
            query = _truncate_for_rerank(pair[0], getattr(self, "max_query_chars", 1600))
            doc = _truncate_for_rerank(pair[1], getattr(self, "max_passage_chars", 1200))
            formatted.append(self._format_instruction(safe_instruction, query, doc))

        probs: List[float] = []
        for batch in self._formatted_batches(formatted):
            probs.extend(self._score_formatted_batch(batch))

        if normalize:
            return probs

        # Keep compatibility with existing callers that expect an unnormalized
        # score while preserving ordering. Mapping p in [0, 1] -> s in [-1, 1]
        # allows downstream `(score + 1) / 2` logic to recover p.
        return [float((p * 2.0) - 1.0) for p in probs]



class _DenseEmbeddingReranker:
    """SentenceTransformer-compatible dense reranker for embedding-only models.

    Harrier is a Sentence Transformers embedding model, not a FlagEmbedding
    cross-encoder. This wrapper keeps the existing compute_score interface while
    using Harrier query/document embeddings and dot-product scoring.
    """

    def __init__(self, model_name_or_path: str, device: str):
        self.model_name_or_path = model_name_or_path
        self.model = get_shared_embedding_model(
            model_name_or_path,
            device=device,
            hf_home=os.getenv("HF_HOME", "/root/.cache/huggingface"),
            trust_remote_code=True,
            dtype_env_key="RERANK_MODEL_DTYPE",
        )

    @staticmethod
    def _normalize_model_ref(value: str | None) -> str:
        return str(value or "").strip().rstrip("/").lower()

    def can_reuse_document_embeddings(self, embedding_model_name_or_path: str | None) -> bool:
        """Return True only when dense rerank and document embeddings use the same model.

        Reusing report_content.embeddings is correct for Harrier when embedding and
        rerank point to the same bi-encoder. If a future reranker uses a different
        model, callers should fall back to compute_score(), which encodes passages
        with that reranker instead of mixing vector spaces.
        """
        rerank_ref = self._normalize_model_ref(self.model_name_or_path)
        embedding_ref = self._normalize_model_ref(embedding_model_name_or_path)
        return bool(rerank_ref and embedding_ref and rerank_ref == embedding_ref)

    @staticmethod
    def _format_query(query: str, instruction: str | None = None) -> str:
        query = str(query or "")
        if instruction:
            return f"{instruction}\n{query}"
        return query

    @staticmethod
    def _normalize_matrix(values) -> np.ndarray:
        matrix = np.asarray(values, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)

    def _encode_queries(self, queries: Sequence[str], batch_size: int):
        return encode_query_texts(
            self.model,
            list(queries),
            model_name_or_path=self.model_name_or_path,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

    def compute_score_from_embeddings(
        self,
        query: str,
        document_embeddings,
        normalize: bool = False,
        instruction: str | None = None,
    ):
        """Score documents by reusing precomputed report embeddings.

        This avoids re-encoding candidate passages for every metric. The only GPU
        work per metric is the query embedding; document vectors stay in the
        report_content.embeddings vector space that was already generated earlier.
        """
        if document_embeddings is None:
            return []
        doc_vecs = self._normalize_matrix(document_embeddings)
        if doc_vecs.size == 0:
            return []

        batch_size = max(1, int(os.getenv("LOCAL_RERANKER_EMBED_BATCH_SIZE", os.getenv("LOCAL_RERANKER_BATCH_SIZE", "64")) or "64"))
        query_text = self._format_query(query, instruction=instruction)
        query_vec = self._normalize_matrix(self._encode_queries([query_text], batch_size=batch_size))[0]
        scores = doc_vecs @ query_vec
        values = [float(x) for x in scores.tolist()]
        if normalize:
            return [max(0.0, min(1.0, (s + 1.0) / 2.0)) for s in values]
        return values

    def compute_score(self, sentence_pairs: Sequence[Sequence[str]], normalize: bool = False, instruction: str | None = None):
        if not sentence_pairs:
            return []

        queries: List[str] = []
        docs: List[str] = []
        for pair in sentence_pairs:
            if len(pair) < 2:
                raise ValueError("Each rerank pair must contain [query, document]")
            queries.append(self._format_query(str(pair[0] or ""), instruction=instruction))
            docs.append(str(pair[1] or ""))

        batch_size = max(1, int(os.getenv("LOCAL_RERANKER_EMBED_BATCH_SIZE", os.getenv("LOCAL_RERANKER_BATCH_SIZE", "64")) or "64"))
        query_vecs = self._normalize_matrix(self._encode_queries(queries, batch_size=batch_size))
        doc_vecs = self._normalize_matrix(self.model.encode(
            docs,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ))

        scores = [float((qv * dv).sum()) for qv, dv in zip(query_vecs, doc_vecs)]

        if normalize:
            return [max(0.0, min(1.0, (s + 1.0) / 2.0)) for s in scores]
        return scores


def _should_use_qwen_reranker(model_name_or_path: str) -> bool:
    normalized = str(model_name_or_path or "").strip().rstrip("/").lower()
    return "qwen" in normalized and "rerank" in normalized


def _should_use_dense_embedding_reranker(model_name_or_path: str) -> bool:
    mode = str(os.getenv("LOCAL_RERANKER_MODE", "") or "").strip().lower()
    if mode in ("dense", "embedding", "bi-encoder", "biencoder"):
        return True
    if mode in ("flag", "cross-encoder", "cross_encoder"):
        return False
    return "harrier-oss" in str(model_name_or_path or "").lower()


class _FlagRerankerAdapter:
    """Compatibility adapter that keeps reranker scores normalized when requested."""

    def __init__(self, reranker):
        self.reranker = reranker

    @staticmethod
    def _normalize_raw_scores(scores):
        values = [float(x) for x in scores]
        if not values:
            return []
        if all(0.0 <= v <= 1.0 for v in values):
            return [max(0.0, min(1.0, v)) for v in values]
        return [float(1.0 / (1.0 + np.exp(-v))) for v in values]

    def compute_score(self, sentence_pairs: Sequence[Sequence[str]], normalize: bool = False, instruction: str | None = None):
        try:
            scores = self.reranker.compute_score(sentence_pairs, normalize=normalize, instruction=instruction)
        except TypeError:
            try:
                scores = self.reranker.compute_score(sentence_pairs, normalize=normalize)
            except TypeError:
                try:
                    scores = self.reranker.compute_score(sentence_pairs, instruction=instruction)
                except TypeError:
                    scores = self.reranker.compute_score(sentence_pairs)
                if normalize:
                    if not isinstance(scores, list):
                        scores = [scores]
                    return self._normalize_raw_scores(scores)
        if normalize:
            if not isinstance(scores, list):
                scores = [scores]
            return [max(0.0, min(1.0, float(x))) for x in scores]
        return scores

def _load_flag_reranker(model_name_or_path: str, device: str, use_fp16: bool):
    """Lazy-load FlagEmbedding reranker, preferring local HF cache."""
    try:
        from FlagEmbedding import FlagReranker  # type: ignore
    except Exception as e:
        raise RuntimeError("FlagEmbedding is not installed. Add FlagEmbedding to requirements.") from e

    # Prefer local snapshot path if model_name_or_path looks like a HF repo id.
    if "/" in model_name_or_path and not model_name_or_path.startswith("/"):
        ref = prefer_local_model(model_name_or_path)
        preferred = ref.local_path or model_name_or_path
    else:
        preferred = model_name_or_path

    return _FlagRerankerAdapter(FlagReranker(preferred, use_fp16=use_fp16, devices=[device]))


def _load_reranker(model_name_or_path: str, device: str, use_fp16: bool):
    model_name = (model_name_or_path or "").strip()
    if _should_use_dense_embedding_reranker(model_name):
        return _DenseEmbeddingReranker(model_name, device=device)
    if _should_use_qwen_reranker(model_name):
        return _QwenReranker(model_name, device=device, use_fp16=use_fp16)
    return _load_flag_reranker(model_name, device=device, use_fp16=use_fp16)


def get_reranker(settings: HippoRAGSettings):
    if not getattr(settings, "rerank_enabled", False):
        return None

    rerank_mode = str(os.getenv("LOCAL_RERANKER_MODE", "") or "").strip().lower()
    rerank_dtype = get_configured_rerank_model_dtype("float16" if bool(settings.rerank_use_fp16) else "float32")
    key = (settings.rerank_model_name_or_path, settings.rerank_device, bool(settings.rerank_use_fp16), rerank_mode, rerank_dtype)

    cached = _cached_rerankers.get(key)
    if cached is not None:
        return cached

    with _lock:
        cached = _cached_rerankers.get(key)
        if cached is not None:
            return cached

        t0 = time.time()
        rr = _load_reranker(settings.rerank_model_name_or_path, settings.rerank_device, settings.rerank_use_fp16)
        _cached_rerankers[key] = rr
        logger.info(f"[Rerank] loaded model={key[0]} device={key[1]} fp16={key[2]} dtype={key[4]} in {time.time()-t0:.2f}s")
        return rr


def rerank_segment_ids(
    query: str,
    scored: List[Tuple[str, float]],
    get_passage: Callable[[str], str],
    settings: HippoRAGSettings,
) -> List[Tuple[str, float]]:
    """Rerank topK segment IDs using FlagEmbedding cross-encoder.

    Input `scored` must already be sorted by fused_score desc.
    Output keeps tail order; only reorders topK.
    """
    if not getattr(settings, "rerank_enabled", False) or not scored:
        return scored

    try:
        rr = get_reranker(settings)
    except Exception as e:
        logger.warning(f"[Rerank] unavailable, skip. {e}")
        return scored

    if rr is None:
        return scored

    k = min(int(settings.rerank_top_k), len(scored))
    head = scored[:k]
    tail = scored[k:]

    max_chars = int(getattr(settings, "rerank_max_chars_per_passage", 2000))
    passages: List[str] = []
    for sid, _ in head:
        p = get_passage(sid) or ""
        if max_chars > 0 and len(p) > max_chars:
            p = p[:max_chars]
        passages.append(p)

    pairs = [[query, p] for p in passages]

    bs = max(1, int(settings.rerank_batch_size))
    t0 = time.time()

    scores: List[float] = []
    for i in range(0, len(pairs), bs):
        chunk = pairs[i : i + bs]
        try:
            s = rr.compute_score(chunk, normalize=True)
        except TypeError:
            s = rr.compute_score(chunk)
        scores.extend([max(0.0, min(1.0, float(x))) for x in s])

    reranked = [(sid, float(rs)) for (sid, _), rs in zip(head, scores)]
    reranked.sort(key=lambda x: x[1], reverse=True)

    logger.info(f"[Rerank] topK={k} bs={bs} took {time.time()-t0:.2f}s")
    return reranked + tail


def clear_reranker_models() -> int:
    """Unload all cached reranker models and clear CUDA cache."""
    import gc

    try:
        import torch  # type: ignore
    except Exception:  # pragma: no cover
        torch = None

    with _lock:
        rerankers = list(_cached_rerankers.values())
        _cached_rerankers.clear()

    count = len(rerankers)
    for rr in rerankers:
        try:
            model = getattr(rr, "model", None)
            if model is not None and hasattr(model, "to"):
                model.to("cpu")
        except Exception:
            pass
        try:
            tokenizer = getattr(rr, "tokenizer", None)
            if tokenizer is not None:
                del tokenizer
        except Exception:
            pass

    del rerankers
    gc.collect()
    try:
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
            try:
                torch.cuda.ipc_collect()
            except Exception:
                pass
    except Exception:
        pass

    logger.info("[Rerank] cleared cached reranker models")
    return count
