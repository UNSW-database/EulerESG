"""
简化的报告编码器

整合PDF提取、段落标签和BGE-M3嵌入的完整流程。
"""

from pathlib import Path
from loguru import logger

from .models import ProcessingConfig, ReportContent
from .content_extractor import ContentExtractor
from .content_embedder import ContentEmbedder
from .exceptions import ESGEncodingError


class ReportEncoder:
    """
    简化的报告编码器
    
    完整的披露导向报告编码流程：
    1. PDF文本提取 + 段落标签
    2. BGE-M3嵌入生成
    3. Markdown格式输出
    """
    
    def __init__(self, config: ProcessingConfig = None):
        """初始化编码器"""
        self.config = config or ProcessingConfig()
        self.logger = logger.bind(component="ReportEncoder")
        
        # 初始化各模块
        self.extractor = ContentExtractor(self.config)
        self.embedder = ContentEmbedder(self.config)
    
    def encode_pdf(self, pdf_path: str, save_markdown: bool = True) -> ReportContent:
        """
        编码PDF报告
        
        Args:
            pdf_path: PDF文件路径
            save_markdown: 是否保存markdown文件
            
        Returns:
            编码后的报告内容
        """
        try:
            self.logger.info(f"开始编码PDF报告: {pdf_path}")
            
            # 步骤1: 提取PDF内容 + 生成段落标签
            document_content = self.extractor.extract_pdf(pdf_path)
            
            # 步骤2: 生成BGE-M3嵌入
            report_content = self.embedder.embed_document(document_content)
            
            # 步骤3: 保存markdown（可选）
            if save_markdown:
                markdown_path = self.extractor.save_markdown(document_content)
                self.logger.info(f"Markdown已保存: {markdown_path}")
            
            self.logger.info(f"报告编码完成: {len(report_content.embeddings)} 个嵌入向量")
            return report_content
            
        except Exception as e:
            self.logger.error(f"报告编码失败: {e}")
            raise ESGEncodingError(f"报告编码失败: {e}")
    
    def search_content(self, report_content: ReportContent, query: str, top_k: int = 5):
        """
        搜索报告内容
        
        Args:
            report_content: 报告内容
            query: 查询文本
            top_k: 返回前k个结果
            
        Returns:
            搜索结果
        """
        try:
            # 计算相似度
            similarities = self.embedder.compute_similarity(query, report_content, top_k)
            
            # 获取对应的段落内容
            results = []
            segment_map = {seg.segment_id: seg for seg in report_content.document_content.segments}
            
            for segment_id, similarity in similarities:
                if segment_id in segment_map:
                    segment = segment_map[segment_id]
                    results.append({
                        'segment_id': segment_id,
                        'content': segment.content,
                        'page_number': segment.page_number,
                        'similarity': similarity
                    })
            
            return results
            
        except Exception as e:
            self.logger.error(f"搜索失败: {e}")
            raise ESGEncodingError(f"搜索失败: {e}")
    
    def get_report_summary(self, report_content: ReportContent):
        """
        获取报告摘要信息
        
        Args:
            report_content: 报告内容
            
        Returns:
            摘要信息
        """
        segments = report_content.document_content.segments
        
        # 统计页面分布
        page_counts = {}
        for segment in segments:
            page_counts[segment.page_number] = page_counts.get(segment.page_number, 0) + 1
        
        return {
            'document_id': report_content.document_id,
            'file_path': report_content.document_content.file_path,
            'total_segments': len(segments),
            'total_embeddings': len(report_content.embeddings),
            'total_pages': max(page_counts.keys()) if page_counts else 0,
            'segments_per_page': page_counts,
            'created_at': report_content.created_at.isoformat()
        } 