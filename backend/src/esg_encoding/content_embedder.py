"""
简化的内容嵌入器

直接使用预训练的BGE-M3模型生成文本嵌入。
"""

from typing import List
import os
import numpy as np
import torch
from loguru import logger

from .shared_embedding_model import get_shared_embedding_model
from .gpu_model_lifecycle import backend_lazy_load_enabled
from .embedding_settings import get_configured_embedding_model_name

from .models import TextSegment, DocumentContent, ReportContent, ProcessingConfig
from .exceptions import ContentEmbeddingError
from .retrieval.metric_corpus import (
    MetricRetrievalCorpus,
    attach_metric_embeddings,
    build_metric_retrieval_corpus,
)


class ContentEmbedder:
    """
    简化的内容嵌入器
    
    直接使用BGE-M3模型生成嵌入向量
    """
    
    def __init__(self, config: ProcessingConfig = None):
        """初始化嵌入器"""
        self.config = config or ProcessingConfig()
        self.logger = logger.bind(component="ContentEmbedder")
        
        # 设置设备：尊重 Docker/config 的 cuda 配置，不因 torch.cuda 检查而静默切到 CPU。
        requested_device = os.getenv("LOCAL_EMBEDDINGS_DEVICE") or str(getattr(self.config, "device", "cuda") or "cuda")
        self.device = torch.device(requested_device)
        
        # 模型按需加载：服务启动时不占用显存；真正需要 embedding 时再加载。
        self.model = None
        if not backend_lazy_load_enabled():
            self._load_model()

    def _ensure_model(self):
        if self.model is None:
            self._load_model()
        return self.model

    def _load_model(self):
        """加载嵌入模型（优先本地 HF cache，缺失/损坏才允许远端下载）"""
        try:
            repo_id = str(getattr(self.config, "embedding_model", "") or get_configured_embedding_model_name())
            hf_home = os.getenv("HF_HOME", "/root/.cache/huggingface")
            self.model = get_shared_embedding_model(
                repo_id,
                device=str(self.device),
                hf_home=hf_home,
                trust_remote_code=True,
            )
            self.logger.info(f"模型加载成功，设备: {self.device}")

        except Exception as e:
            raise ContentEmbeddingError(f"模型加载失败: {e}")


    def embed_document(self, document_content: DocumentContent) -> ReportContent:
        """
        为文档生成嵌入
        
        Args:
            document_content: 文档内容

        Returns:
            包含嵌入的报告内容
        """
        try:
            self.logger.info(f"开始生成嵌入: {len(document_content.segments)} 个段落")
            self._ensure_model()
            
            # 准备文本列表
            texts = [segment.content for segment in document_content.segments]
            
            # 批量生成嵌入
            embeddings = self._generate_embeddings(texts)
            
            # Keep one native float32 representation. Building one Python
            # float list per row duplicates the matrix and dominates memory on
            # large reports.
            contiguous = np.ascontiguousarray(embeddings, dtype=np.float32)
            if contiguous.ndim != 2 or contiguous.shape[0] != len(document_content.segments):
                raise ContentEmbeddingError(
                    "Embedding model returned an invalid document matrix: "
                    f"segments={len(document_content.segments)}, shape={contiguous.shape}"
                )

            report_content = ReportContent(
                document_id=document_content.document_id,
                document_content=document_content,
                # Kept empty for legacy model compatibility. New reports use
                # the attached matrix and persist it directly as NPZ.
                embeddings=[],
            )
            object.__setattr__(report_content, "_embedding_matrix", contiguous)
            object.__setattr__(report_content, "_embedding_segment_ids", [s.segment_id for s in document_content.segments])

            # Metric assessment uses a structure-preserving side corpus. Keep
            # it independent from the canonical one-row-per-segment matrix used
            # by chat and persisted report validation.
            metric_enabled = str(
                os.getenv("REPORT_METRIC_CORPUS_ENABLED", "1") or "1"
            ).strip().lower() in {"1", "true", "yes", "y", "on"}
            if metric_enabled:
                try:
                    metric_corpus = build_metric_retrieval_corpus(document_content)
                    self.embed_metric_corpus(metric_corpus)
                    object.__setattr__(
                        report_content,
                        "_metric_retrieval_corpus",
                        metric_corpus,
                    )
                except Exception as metric_error:
                    self.logger.warning(
                        "Metric retrieval corpus generation failed; "
                        f"using canonical fallback: {metric_error}"
                    )

            self.logger.info(f"嵌入生成完成: {contiguous.shape[0]} 个向量")
            return report_content
            
        except Exception as e:
            raise ContentEmbeddingError(f"嵌入生成失败: {e}")

    def embed_metric_corpus(
        self,
        corpus: MetricRetrievalCorpus,
    ) -> MetricRetrievalCorpus:
        """Embed retrieval views without altering canonical report embeddings."""
        views = list(corpus.retrieval_views or [])
        if not views:
            matrix = np.zeros((0, 0), dtype=np.float32)
        else:
            matrix = np.ascontiguousarray(
                self._generate_embeddings([view.index_text for view in views]),
                dtype=np.float32,
            )
            if matrix.ndim != 2 or matrix.shape[0] != len(views):
                raise ContentEmbeddingError(
                    "Embedding model returned an invalid metric retrieval matrix: "
                    f"views={len(views)}, shape={matrix.shape}"
                )
            if not np.isfinite(matrix).all():
                raise ContentEmbeddingError(
                    "Metric retrieval embeddings contain non-finite values"
                )

        return attach_metric_embeddings(
            corpus,
            matrix,
            embedding_model=str(
                getattr(self.config, "embedding_model", "") or ""
            ),
            normalized=True,
        )
    
    def _generate_embeddings(self, texts: List[str]):
        """
        生成文本嵌入
        
        Args:
            texts: 文本列表
            
        Returns:
            嵌入向量数组
        """
        try:
            # 使用模型生成嵌入
            model = self._ensure_model()
            embeddings = model.encode(
                texts,
                batch_size=self.config.batch_size,
                show_progress_bar=True,
                convert_to_numpy=True,
                normalize_embeddings=True
            )
            
            return embeddings
            
        except Exception as e:
            raise ContentEmbeddingError(f"嵌入计算失败: {e}")
    
    def compute_similarity(self, query_text: str, report_content: ReportContent, top_k: int = 10):
        """
        计算查询与段落的相似度
        
        Args:
            query_text: 查询文本
            report_content: 报告内容
            top_k: 返回最相似的前k个段落
            
        Returns:
            相似度结果列表
        """
        try:
            # 生成查询嵌入
            model = self._ensure_model()
            query_embedding = model.encode([query_text], normalize_embeddings=True)[0]
            
            matrix = getattr(report_content, "_embedding_matrix", None)
            segment_ids = getattr(report_content, "_embedding_segment_ids", None)
            if not (
                isinstance(matrix, np.ndarray)
                and matrix.ndim == 2
                and segment_ids is not None
                and len(segment_ids) == matrix.shape[0]
            ):
                legacy = list(report_content.embeddings or [])
                segment_ids = [item.segment_id for item in legacy]
                matrix = (
                    np.asarray([item.embedding for item in legacy], dtype=np.float32)
                    if legacy
                    else np.zeros((0, 0), dtype=np.float32)
                )
            matrix = np.asarray(matrix, dtype=np.float32)
            if matrix.ndim != 2 or matrix.shape[0] == 0:
                return []
            query = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
            if matrix.shape[1] != query.shape[0]:
                raise ContentEmbeddingError(
                    "Query and document embedding dimensions do not match: "
                    f"query={query.shape[0]}, document={matrix.shape[1]}"
                )
            denominators = np.linalg.norm(matrix, axis=1) * max(float(np.linalg.norm(query)), 1e-12)
            scores = (matrix @ query) / np.maximum(denominators, 1e-12)
            limit = min(max(0, int(top_k)), len(segment_ids))
            indexes = np.argsort(-scores, kind="stable")[:limit]
            return [(str(segment_ids[index]), float(scores[index])) for index in indexes]
            
        except Exception as e:
            raise ContentEmbeddingError(f"相似度计算失败: {e}")
