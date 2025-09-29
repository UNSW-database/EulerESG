"""
测试ESG指标处理和双通道检索功能
"""

import pytest
from pathlib import Path
import tempfile
import json
from src.esg_encoding import (
    MetricProcessor, DualChannelRetriever, ProcessingConfig,
    ESGMetric, MetricCategory, MetricSource, ReportEncoder
)


class TestMetricProcessor:
    """测试指标处理器"""
    
    def test_create_sample_metrics(self):
        """测试创建示例指标"""
        config = ProcessingConfig(device="cpu")
        processor = MetricProcessor(config)
        
        collection = processor.create_sample_metrics()
        
        assert len(collection.metrics) == 4
        assert collection.collection_name == "示例ESG指标集合"
        assert collection.metrics[0].metric_name == "碳排放量"
        assert collection.metrics[0].category == MetricCategory.ENVIRONMENTAL
    
    def test_generate_default_description(self):
        """测试生成默认语义描述"""
        config = ProcessingConfig(device="cpu")
        processor = MetricProcessor(config)
        
        metric = ESGMetric(
            metric_id="test_001",
            metric_name="测试指标",
            metric_code="TEST-001",
            category=MetricCategory.ENVIRONMENTAL,
            source=MetricSource.GRI,
            keywords=["测试", "指标"],
            description="这是一个测试指标",
            unit="单位"
        )
        
        description = processor._generate_default_description(metric)
        
        assert "测试指标" in description
        assert "environmental" in description
        assert "测试" in description
        assert "指标" in description
        assert "单位" in description
    
    def test_expand_metric_semantics(self):
        """测试指标语义扩展"""
        config = ProcessingConfig(device="cpu")
        processor = MetricProcessor(config)
        
        metric = ESGMetric(
            metric_id="test_001",
            metric_name="测试指标",
            metric_code="TEST-001",
            category=MetricCategory.ENVIRONMENTAL,
            source=MetricSource.GRI,
            keywords=["测试", "指标"],
            description="这是一个测试指标"
        )
        
        expansion = processor.expand_metric_semantics(metric)
        
        assert expansion.metric_id == "test_001"
        assert expansion.semantic_description
        assert expansion.embedding is not None
        assert len(expansion.embedding) > 0
    
    def test_save_and_load_metric_collection(self):
        """测试保存和加载指标集合"""
        config = ProcessingConfig(device="cpu")
        processor = MetricProcessor(config)
        
        # 创建示例集合
        collection = processor.create_sample_metrics()
        
        # 保存到临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_file = f.name
        
        try:
            processor.save_metric_collection(collection, temp_file)
            
            # 验证文件存在
            assert Path(temp_file).exists()
            
            # 加载并验证
            loaded_collection = processor.load_metrics_from_file(temp_file)
            assert len(loaded_collection.metrics) == len(collection.metrics)
            assert loaded_collection.collection_name == collection.collection_name
            
        finally:
            # 清理临时文件
            Path(temp_file).unlink(missing_ok=True)


class TestDualChannelRetriever:
    """测试双通道检索器"""
    
    def test_keyword_search(self):
        """测试关键词搜索"""
        config = ProcessingConfig(device="cpu", top_k=3)
        retriever = DualChannelRetriever(config)
        
        # 创建模拟数据
        from src.esg_encoding.models import TextSegment, DocumentContent, ReportContent
        
        segments = [
            TextSegment(
                segment_id="P001_S001",
                content="我们公司的碳排放量在2023年显著降低。",
                page_number=1,
                position_y=100
            ),
            TextSegment(
                segment_id="P001_S002", 
                content="能源消耗主要来自电力和天然气。",
                page_number=1,
                position_y=200
            ),
            TextSegment(
                segment_id="P001_S003",
                content="董事会独立性得到了加强。",
                page_number=1,
                position_y=300
            )
        ]
        
        doc_content = DocumentContent(
            document_id="test_doc",
            file_path="test.pdf",
            segments=segments,
            markdown_content="test"
        )
        
        report_content = ReportContent(
            document_id="test_doc",
            document_content=doc_content,
            embeddings=[]
        )
        
        # 创建测试指标
        metric = ESGMetric(
            metric_id="test_001",
            metric_name="碳排放量",
            metric_code="GHG-001",
            category=MetricCategory.ENVIRONMENTAL,
            source=MetricSource.GRI,
            keywords=["碳排放", "排放量"]
        )
        
        # 执行关键词搜索
        results = retriever.keyword_retriever.search_in_report(report_content, metric)
        
        assert len(results) == 1
        assert results[0].segment_id == "P001_S001"
        assert results[0].retrieval_type == "keyword"
        assert "碳排放" in results[0].matched_keywords
    
    def test_combine_results(self):
        """测试结果合并"""
        config = ProcessingConfig(device="cpu", top_k=10)
        retriever = DualChannelRetriever(config)
        
        from src.esg_encoding.models import RetrievalResult
        
        keyword_results = [
            RetrievalResult(
                segment_id="P001_S001",
                content="测试内容1",
                page_number=1,
                score=0.8,
                retrieval_type="keyword",
                matched_keywords=["测试"],
                metric_id="test_001"
            )
        ]
        
        semantic_results = [
            RetrievalResult(
                segment_id="P001_S001",
                content="测试内容1",
                page_number=1,
                score=0.6,
                retrieval_type="semantic",
                matched_keywords=[],
                metric_id="test_001"
            ),
            RetrievalResult(
                segment_id="P001_S002",
                content="测试内容2",
                page_number=1,
                score=0.5,
                retrieval_type="semantic",
                matched_keywords=[],
                metric_id="test_001"
            )
        ]
        
        combined = retriever._combine_results(keyword_results, semantic_results)
        
        assert len(combined) == 2
        assert combined[0].segment_id == "P001_S001"
        assert combined[0].retrieval_type == "keyword+semantic"
        assert combined[0].score == 0.7  # 平均值


def test_integration():
    """集成测试"""
    config = ProcessingConfig(device="cpu", top_k=3)
    
    # 创建处理器
    processor = MetricProcessor(config)
    
    # 创建指标集合
    collection = processor.create_sample_metrics()
    
    # 处理语义扩展
    processed_collection = processor.process_metric_collection(collection)
    
    # 验证结果
    assert len(processed_collection.semantic_expansions) == len(collection.metrics)
    
    # 每个指标都应该有对应的语义扩展
    metric_ids = {metric.metric_id for metric in collection.metrics}
    expansion_ids = {exp.metric_id for exp in processed_collection.semantic_expansions}
    assert metric_ids == expansion_ids


if __name__ == "__main__":
    pytest.main([__file__, "-v"]) 