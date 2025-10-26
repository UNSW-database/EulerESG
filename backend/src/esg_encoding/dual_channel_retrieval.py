"""
Dual-Channel Metrics Retrieval Module

This module implements dual-channel metric retrieval, including:
1. Keyword Retrieval - Keyword-based retrieval
2. Semantic Retrieval - Semantic-based retrieval
"""

import re
import numpy as np
from typing import Dict, List, Optional, Union, Tuple
from loguru import logger
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

# Import reranker - try different import methods
try:
    from FlagEmbedding import FlagReranker
except ImportError:
    try:
        from flagembedding import FlagReranker
    except ImportError:
        logger.warning("FlagEmbedding not available, will use basic cosine similarity")

from .models import (
    ESGMetric, SemanticExpansion, MetricCollection, ReportContent,
    RetrievalResult, MetricRetrievalResult, ProcessingConfig
)
from .exceptions import ESGEncodingError, ContentEmbeddingError


class KeywordRetriever:
    """Keyword retriever"""
    
    def __init__(self, config: ProcessingConfig):
        """
        Initialize keyword retriever
        
        Args:
            config: Processing configuration
        """
        self.config = config
    
    def search_keywords_in_text(self, text: str, keywords: List[str], 
                              case_sensitive: bool = False) -> List[Tuple[str, List[int]]]:
        """
        Search for keywords in text
        
        Args:
            text: Text to search in
            keywords: List of keywords
            case_sensitive: Whether to be case sensitive
            
        Returns:
            List[Tuple[str, List[int]]]: Keywords and their occurrence positions
        """
        results = []
        
        for keyword in keywords:
            # Build regular expression
            pattern = re.escape(keyword)
            flags = 0 if case_sensitive else re.IGNORECASE
            
            # Find all matching positions
            matches = [(m.start(), m.end()) for m in re.finditer(pattern, text, flags)]
            
            if matches:
                results.append((keyword, matches))
        
        return results
    
    def search_in_report(self, report_content: ReportContent, metric: ESGMetric) -> List[RetrievalResult]:
        """
        Search for metric-related content in report
        
        Args:
            report_content: Report content
            metric: ESG metric
            
        Returns:
            List[RetrievalResult]: Retrieval results
        """
        results = []
        
        for segment in report_content.document_content.segments:
            # Search keywords
            keyword_matches = self.search_keywords_in_text(
                segment.content, 
                metric.keywords
            )
            
            if keyword_matches:
                # Calculate keyword matching score
                matched_keywords = [kw for kw, _ in keyword_matches]
                total_matches = sum(len(positions) for _, positions in keyword_matches)
                
                # Simple score calculation: matched keywords count / total keywords count
                score = len(matched_keywords) / len(metric.keywords) if metric.keywords else 0
                
                result = RetrievalResult(
                    segment_id=segment.segment_id,
                    content=segment.content,
                    page_number=segment.page_number,
                    score=score,
                    retrieval_type="keyword",
                    matched_keywords=matched_keywords,
                    metric_id=metric.metric_id
                )
                results.append(result)
        
        # Sort by score
        results.sort(key=lambda x: x.score, reverse=True)
        
        logger.info(f"Keyword retrieval for metric {metric.metric_id} found {len(results)} results")
        return results[:self.config.top_k]


class SemanticRetriever:
    """Semantic retriever"""
    
    def __init__(self, config: ProcessingConfig):
        """
        Initialize semantic retriever
        
        Args:
            config: Processing configuration
        """
        self.config = config
        self.embedding_model = None
        self.reranker = None
        self._init_embedding_model()
        self._init_reranker()
    
    def _init_embedding_model(self):
        """Initialize embedding model"""
        try:
            logger.info(f"Loading embedding model: {self.config.embedding_model}")
            self.embedding_model = SentenceTransformer(
                self.config.embedding_model,
                device=self.config.device
            )
            logger.info("Embedding model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {str(e)}")
            raise ContentEmbeddingError(f"Failed to load embedding model: {str(e)}")
    
    def _init_reranker(self):
        """Initialize BGE reranker model"""
        try:
            # Try to load BGE-Reranker-v2-M3
            if 'FlagReranker' in globals():
                logger.info("Loading BGE-Reranker-v2-M3 model...")
                self.reranker = FlagReranker('BAAI/bge-reranker-v2-m3', use_fp16=True)
                logger.info("BGE-Reranker-v2-M3 model loaded successfully")
            else:
                logger.warning("FlagReranker not available, will use basic cosine similarity")
                self.reranker = None
        except Exception as e:
            logger.warning(f"Failed to load reranker model, fallback to cosine similarity: {str(e)}")
            self.reranker = None
    
    def search_by_semantic(self, report_content: ReportContent, 
                          semantic_expansion: SemanticExpansion) -> List[RetrievalResult]:
        """
        Search by semantic similarity
        
        Args:
            report_content: Report content
            semantic_expansion: Semantic expansion
            
        Returns:
            List[RetrievalResult]: Retrieval results
        """
        try:
            # Get metric's semantic embedding
            if not semantic_expansion.embedding:
                raise ValueError("No embedding vector in semantic expansion")
            
            query_embedding = np.array(semantic_expansion.embedding).reshape(1, -1)
            
            # Get report segments' embeddings
            segment_embeddings = []
            segments = []
            
            for segment_emb in report_content.embeddings:
                segment_embeddings.append(segment_emb.embedding)
                # Find corresponding segment
                for seg in report_content.document_content.segments:
                    if seg.segment_id == segment_emb.segment_id:
                        segments.append(seg)
                        break
            
            if not segment_embeddings:
                logger.warning("No embedding vectors found in report")
                return []
            
            # Use BGE-Reranker-v2-M3 if available, otherwise fallback to cosine similarity
            if self.reranker is not None:
                # Use BGE-Reranker-v2-M3 for more accurate relevance scoring
                query_text = semantic_expansion.semantic_description
                
                # Prepare query-document pairs for reranker
                query_doc_pairs = [[query_text, segment.content] for segment in segments]
                
                # Get reranker scores
                rerank_scores = self.reranker.compute_score(query_doc_pairs)
                if not isinstance(rerank_scores, list):
                    rerank_scores = [rerank_scores]  # Handle single score case
                
                # Create results with reranker scores
                results = []
                for i, (segment, score) in enumerate(zip(segments, rerank_scores)):
                    # Convert reranker score to 0-1 range (reranker outputs can be negative)
                    normalized_score = max(0, min(1, (score + 1) / 2))  # Normalize from [-1,1] to [0,1]
                    
                    if normalized_score >= self.config.similarity_threshold:
                        result = RetrievalResult(
                            segment_id=segment.segment_id,
                            content=segment.content,
                            page_number=segment.page_number,
                            score=float(normalized_score),
                            retrieval_type="semantic+rerank",
                            matched_keywords=[],
                            metric_id=semantic_expansion.metric_id
                        )
                        results.append(result)
                
                logger.info(f"Used BGE-Reranker-v2-M3 for semantic retrieval")
            else:
                # Fallback to cosine similarity
                segment_embeddings = np.array(segment_embeddings)
                similarities = cosine_similarity(query_embedding, segment_embeddings)[0]
                
                # Create results with cosine similarity
                results = []
                for i, (segment, similarity) in enumerate(zip(segments, similarities)):
                    if similarity >= self.config.similarity_threshold:
                        result = RetrievalResult(
                            segment_id=segment.segment_id,
                            content=segment.content,
                            page_number=segment.page_number,
                            score=float(similarity),
                            retrieval_type="semantic",
                            matched_keywords=[],
                            metric_id=semantic_expansion.metric_id
                        )
                        results.append(result)
                
                logger.info(f"Used cosine similarity fallback for semantic retrieval")
            
            # Sort by score
            results.sort(key=lambda x: x.score, reverse=True)
            
            logger.info(f"Semantic retrieval for metric {semantic_expansion.metric_id} found {len(results)} results")
            return results[:self.config.top_k]
            
        except Exception as e:
            logger.error(f"Semantic retrieval failed: {str(e)}")
            raise ESGEncodingError(f"Semantic retrieval failed: {str(e)}")


class DualChannelRetriever:
    """Dual-channel retriever"""
    
    def __init__(self, config: ProcessingConfig):
        """
        Initialize dual-channel retriever
        
        Args:
            config: Processing configuration
        """
        self.config = config
        self.keyword_retriever = KeywordRetriever(config)
        self.semantic_retriever = SemanticRetriever(config)
    
    def retrieve_for_metric(self, report_content: ReportContent, 
                          metric: ESGMetric, 
                          semantic_expansion: Optional[SemanticExpansion] = None) -> MetricRetrievalResult:
        """
        Perform dual-channel retrieval for a single metric
        
        Args:
            report_content: Report content
            metric: ESG metric
            semantic_expansion: Semantic expansion (optional)
            
        Returns:
            MetricRetrievalResult: Metric retrieval result
        """
        try:
            logger.info(f"Starting dual-channel retrieval for metric {metric.metric_name}")
            
            # Keyword retrieval
            keyword_results = self.keyword_retriever.search_in_report(report_content, metric)
            
            # Semantic retrieval
            semantic_results = []
            if semantic_expansion:
                semantic_results = self.semantic_retriever.search_by_semantic(
                    report_content, semantic_expansion
                )
            
            # Combine results
            combined_results = self._combine_results(keyword_results, semantic_results)
            
            result = MetricRetrievalResult(
                metric_id=metric.metric_id,
                metric_name=metric.metric_name,
                metric_code=metric.metric_code,
                keyword_results=keyword_results,
                semantic_results=semantic_results,
                combined_results=combined_results,
                total_matches=len(combined_results)
            )
            
            logger.info(f"Retrieval completed for metric {metric.metric_name}, found {len(combined_results)} results")
            return result
            
        except Exception as e:
            logger.error(f"Metric retrieval failed: {str(e)}")
            raise ESGEncodingError(f"Metric retrieval failed: {str(e)}")
    
    def _combine_results(self, keyword_results: List[RetrievalResult], 
                        semantic_results: List[RetrievalResult]) -> List[RetrievalResult]:
        """
        Combine keyword and semantic retrieval results
        
        Args:
            keyword_results: Keyword retrieval results
            semantic_results: Semantic retrieval results
            
        Returns:
            List[RetrievalResult]: Combined results
        """
        # Use dictionary for deduplication (based on segment_id)
        combined_dict = {}
        
        # Add keyword retrieval results
        for result in keyword_results:
            combined_dict[result.segment_id] = result
        
        # Add semantic retrieval results, merge scores if already exists
        for result in semantic_results:
            if result.segment_id in combined_dict:
                # Merge scores with different weights based on retrieval type
                existing = combined_dict[result.segment_id]
                
                # Give higher weight to reranker results
                if result.retrieval_type == "semantic+rerank":
                    # Weight: 0.3 keyword + 0.7 reranker
                    combined_score = (existing.score * 0.3) + (result.score * 0.7)
                    existing.retrieval_type = "keyword+rerank"
                else:
                    # Original simple average for cosine similarity
                    combined_score = (existing.score + result.score) / 2
                    existing.retrieval_type = "keyword+semantic"
                
                existing.score = combined_score
            else:
                combined_dict[result.segment_id] = result
        
        # Convert to list and sort
        combined_results = list(combined_dict.values())
        combined_results.sort(key=lambda x: x.score, reverse=True)
        
        return combined_results[:self.config.top_k]
    
    def retrieve_for_collection(self, report_content: ReportContent, 
                              metric_collection: MetricCollection) -> List[MetricRetrievalResult]:
        """
        Perform dual-channel retrieval for metric collection
        
        Args:
            report_content: Report content
            metric_collection: Metric collection
            
        Returns:
            List[MetricRetrievalResult]: Retrieval results for all metrics
        """
        results = []
        
        # Create semantic expansion mapping
        expansions_map = {
            exp.metric_id: exp 
            for exp in metric_collection.semantic_expansions
        }
        
        for metric in metric_collection.metrics:
            logger.info(f"Retrieving metric: {metric.metric_name}")
            
            # Get corresponding semantic expansion
            semantic_expansion = expansions_map.get(metric.metric_id)
            
            # Perform retrieval
            result = self.retrieve_for_metric(
                report_content, 
                metric, 
                semantic_expansion
            )
            results.append(result)
        
        logger.info(f"Completed metric collection retrieval, processed {len(results)} metrics")
        return results
    
    def generate_retrieval_report(self, retrieval_results: List[MetricRetrievalResult]) -> str:
        """
        Generate retrieval report
        
        Args:
            retrieval_results: List of retrieval results
            
        Returns:
            str: Retrieval report (Markdown format)
        """
        report_lines = [
            "# ESG Metric Retrieval Report\n",
            f"**Generated Time**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            f"**Number of Retrieved Metrics**: {len(retrieval_results)}\n",
            f"**Total Matched Segments**: {sum(r.total_matches for r in retrieval_results)}\n",
            "---\n"
        ]
        
        for result in retrieval_results:
            report_lines.extend([
                f"## {result.metric_name} ({result.metric_id})\n",
                f"**Total Matches**: {result.total_matches}\n",
                f"**Keyword Matches**: {len(result.keyword_results)}\n",
                f"**Semantic Matches**: {len(result.semantic_results)}\n",
                ""
            ])
            
            # Show top 5 best matches
            if result.combined_results:
                report_lines.append("### Best Matching Segments\n")
                for i, match in enumerate(result.combined_results[:5], 1):
                    report_lines.extend([
                        f"**{i}. Segment {match.segment_id}** (Page: {match.page_number}, Score: {match.score:.3f})\n",
                        f"*Retrieval Type: {match.retrieval_type}*\n",
                        f"*Matched Keywords: {', '.join(match.matched_keywords) if match.matched_keywords else 'None'}*\n",
                        f"```\n{match.content[:200]}...\n```\n",
                        ""
                    ])
            
            report_lines.append("---\n")
        
        return "\n".join(report_lines)


from datetime import datetime 