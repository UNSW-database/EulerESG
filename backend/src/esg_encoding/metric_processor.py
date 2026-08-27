"""
Standards-Based Metric Extraction & Expansion 模块

该模块负责：
1. Standard Metric Identification (SMI) - 标准指标识别
2. Standard-Aligned Metrics Refinement - 标准对齐指标精炼
3. Semantic Expansion of Metric Definitions - 指标定义的语义扩展
"""

import json
import uuid
import math
import re
import os
from typing import Dict, List, Optional, Union
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from loguru import logger
import openai
import pandas as pd
import torch

from .models import (
    ESGMetric, MetricCategory, MetricSource, SemanticExpansion, 
    MetricCollection, ProcessingConfig
)
from .exceptions import ESGEncodingError, ContentEmbeddingError
from .shared_embedding_model import encode_query_texts, get_shared_embedding_model
from .gpu_model_lifecycle import backend_lazy_load_enabled

_SASB_METRICS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sasb_metrics"
_CDP_METRICS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cdp_metrics"
_CDP_TOPIC_SLUGS = frozenset(
    {
        "Organization",
        "Risk_and_Impact",
        "Risk_Disclosure",
        "Governance",
        "Strategy",
        "Climate",
        "Water",
        "Biodiversity",
    }
)
_TCFD_METRICS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "tcfd_metrics"
_TCFD_TOPIC_SLUGS = frozenset(
    {
        "Governance",
        "Strategy",
        "Risk_Management",
        "Metrics_and_Targets",
    }
)
_SASB_MANIFEST_PATH = _SASB_METRICS_DIR / "manifest.json"
_SASB_INDUSTRY_FILE_MAP_FALLBACK: Dict[str, str] = {
    "Software & IT Services": "software_&_it_services.json",
    "Hardware": "Hardware.json",
    "Semiconductors": "semiconductors.json",
    "Internet Media & Services": "Internet_Media_and_Services.json",
    "Telecommunication Services": "telecommunication_services.json",
    "Electronic Manufacturing Services & Original Design Manufacturing": "Electronic_Manufacturing_Servic.json",
    "Investment Banking & Brokerage": "Investment_Banking_and_Brokerage.json",
    "Commercial Banks": "Commercial_Banks.json",
    "Asset Management & Custody Activities": "Asset_Management_and_Custody_Activities.json",
    "E-Commerce": "E-Commerce.json",
    "Apparel, Accessories & Footwear": "Apparel_Accessories_and_Footwear.json",
    "Household & Personal Products": "Household_and_Personal_Products.json",
    "Multiline and Specialty Retailers & Distributors": "Multiline_and_Specialty_Retailers_and_Distributors.json",
    "Automobiles": "Automobiles.json",
    "Auto Parts": "Auto_Parts.json",
    "Car Rental & Leasing": "Car_Rental_and_Leasing.json",
}
_sasb_industry_file_map_cache: Optional[Dict[str, str]] = None


def _load_sasb_industry_file_mapping() -> Dict[str, str]:
    """semi_industry label -> JSON filename under data/sasb_metrics (from manifest.json)."""
    global _sasb_industry_file_map_cache
    if _sasb_industry_file_map_cache is not None:
        return _sasb_industry_file_map_cache
    if _SASB_MANIFEST_PATH.exists():
        with open(_SASB_MANIFEST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _sasb_industry_file_map_cache = dict(data.get("semi_industry_to_file", {}))
    else:
        _sasb_industry_file_map_cache = dict(_SASB_INDUSTRY_FILE_MAP_FALLBACK)
    return _sasb_industry_file_map_cache


def _reload_sasb_industry_file_mapping() -> Dict[str, str]:
    """Force refresh the SASB manifest mapping (used when runtime files changed)."""
    global _sasb_industry_file_map_cache
    _sasb_industry_file_map_cache = None
    return _load_sasb_industry_file_mapping()


class MetricProcessor:
    """指标处理器 - 负责指标的提取、精炼和语义扩展"""
    
    def __init__(self, config: ProcessingConfig):
        """
        初始化指标处理器
        
        Args:
            config: 处理配置
        """
        self.config = config
        self.embedding_model = None
        self.llm_client = None
        
        # 嵌入模型按需加载：只有语义扩展真正需要 embedding 时才加载。
        if not backend_lazy_load_enabled():
            self._init_embedding_model()
        
        # 初始化LLM客户端
        if config.llm_api_key:
            self._init_llm_client()
    
    def _ensure_embedding_model(self):
        if self.embedding_model is None:
            self._init_embedding_model()
        return self.embedding_model

    def _init_embedding_model(self):
        """初始化嵌入模型"""
        try:
            # 检查CUDA是否可用，如果不可用则使用CPU
            device = torch.device(self.config.device if torch.cuda.is_available() else "cpu")
            logger.info(f"正在加载嵌入模型: {self.config.embedding_model}")
            self.embedding_model = get_shared_embedding_model(
                self.config.embedding_model,
                device=str(device),
                hf_home=os.getenv("HF_HOME", "/root/.cache/huggingface"),
                trust_remote_code=True,
            )
            logger.info(f"嵌入模型加载成功，设备: {device}")
        except Exception as e:
            logger.error(f"嵌入模型加载失败: {str(e)}")
            raise ContentEmbeddingError(f"嵌入模型加载失败: {str(e)}")
    
    def _init_llm_client(self):
        """初始化LLM客户端"""
        if not self.config.llm_api_key:
            raise ValueError("LLM API key is required for metric processing. Please configure LLM_API_KEY in your .env file.")

        base_url = self.config.llm_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"

        self.llm_client = openai.OpenAI(
            api_key=self.config.llm_api_key,
            base_url=base_url
        )
        logger.info("Metric processor LLM client initialized successfully")
    
    def load_metrics_from_file(self, file_path: Union[str, Path]) -> MetricCollection:
        """
        从文件加载指标数据
        
        Args:
            file_path: 指标文件路径
            
        Returns:
            MetricCollection: 指标集合
        """
        try:
            file_path = Path(file_path)
            
            if not file_path.exists():
                raise FileNotFoundError(f"指标文件不存在: {file_path}")
            
            # 根据文件扩展名选择加载方式
            if file_path.suffix.lower() == '.json':
                return self._load_from_json(file_path)
            elif file_path.suffix.lower() in ['.xlsx', '.xls']:
                return self._load_from_excel(file_path)
            else:
                raise ValueError(f"不支持的文件格式: {file_path.suffix}")
                
        except Exception as e:
            logger.error(f"加载指标文件失败: {str(e)}")
            raise ESGEncodingError(f"加载指标文件失败: {str(e)}")
    
    def load_metrics_from_excel(self, excel_path: Union[str, Path]) -> MetricCollection:
        """
        从Excel文件加载ESG指标
        
        Args:
            excel_path: Excel文件路径
            
        Returns:
            MetricCollection: 加载的指标集合
        """
        return self._load_from_excel(Path(excel_path))
    
    def _load_from_json(self, file_path: Path) -> MetricCollection:
        """从JSON文件加载指标数据"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 解析指标数据
        metrics = []
        for metric_data in data.get('metrics', []):
            metric = ESGMetric(
                metric_id=metric_data.get('metric_id', str(uuid.uuid4())),
                metric_name=metric_data['metric_name'],
                metric_code=metric_data['metric_code'],
                category=MetricCategory(metric_data.get('category', 'general')),
                source=MetricSource(metric_data.get('source', 'custom')),
                keywords=metric_data.get('keywords', []),
                description=metric_data.get('description', ''),
                definition=(metric_data.get('definition') or metric_data.get('Definition') or ''),
                unit=metric_data.get('unit')
            )
            metrics.append(metric)
        
        collection = MetricCollection(
            collection_id=data.get('collection_id', str(uuid.uuid4())),
            collection_name=data.get('collection_name', 'Default Collection'),
            metrics=metrics
        )
        
        logger.info(f"成功从JSON加载 {len(metrics)} 个指标")
        return collection
    
    def _load_from_excel(self, file_path: Path) -> MetricCollection:
        """从Excel文件加载指标数据"""
        try:
            # 读取Excel文件
            df = pd.read_excel(file_path)
            
            # 显示列名以便调试
            logger.info(f"Excel文件列名: {list(df.columns)}")
            
            # 标准化列名映射 - 根据实际Excel文件调整
            column_mapping = {
                'metric_name': ['Metric', 'metric_name', 'name', '指标名称', 'indicator_name'],
                'metric_code': ['Code', 'metric_code', 'code', '指标代码', 'indicator_code'],
                'category': ['Category', 'category', 'type', '类别', 'indicator_type'],
                'source': ['source', 'standard', '来源', 'standard_source'],
                'keywords': ['Topic', 'keywords', 'key_words', '关键词', 'key_terms'],
                'description': ['Context', 'description', 'desc', '描述'],
                'definition': ['definition', 'Definition', 'Definitions', '定义'],
                'unit': ['Unit', 'unit', 'units', '单位', 'measurement_unit']
            }
            
            # 找到实际的列名
            actual_columns = {}
            for std_col, possible_cols in column_mapping.items():
                for col in possible_cols:
                    if col in df.columns:
                        actual_columns[std_col] = col
                        break
            
            logger.info(f"映射的列名: {actual_columns}")
            
            # 解析指标数据
            metrics = []
            seen_metrics = set()  # 用于去重
            for index, row in df.iterrows():
                try:
                    # 提取基本信息 - 必须字段不能使用默认值
                    metric_name_col = actual_columns.get('metric_name', '')
                    metric_code_col = actual_columns.get('metric_code', '')

                    if not metric_name_col or pd.isna(row.get(metric_name_col)):
                        raise ValueError(f"Row {index+1}: Missing required field 'metric_name'")
                    if not metric_code_col or pd.isna(row.get(metric_code_col)):
                        raise ValueError(f"Row {index+1}: Missing required field 'metric_code'")

                    metric_name = str(row[metric_name_col]).strip()
                    metric_code = str(row[metric_code_col]).strip()

                    if not metric_name or metric_name == 'nan':
                        raise ValueError(f"Row {index+1}: Empty metric_name")
                    if not metric_code or metric_code == 'nan':
                        raise ValueError(f"Row {index+1}: Empty metric_code")
                    
                    # 处理类别
                    category_value = str(row.get(actual_columns.get('category', ''), 'general')).lower()
                    if 'environment' in category_value or '环境' in category_value:
                        category = MetricCategory.ENVIRONMENTAL
                    elif 'social' in category_value or '社会' in category_value:
                        category = MetricCategory.SOCIAL
                    elif 'governance' in category_value or '治理' in category_value:
                        category = MetricCategory.GOVERNANCE
                    else:
                        category = MetricCategory.GENERAL
                    
                    # 处理来源
                    source_value = str(row.get(actual_columns.get('source', ''), 'custom')).lower()
                    if 'gri' in source_value:
                        source = MetricSource.GRI
                    elif 'sasb' in source_value:
                        source = MetricSource.SASB
                    elif 'cdp' in source_value:
                        source = MetricSource.CDP
                    elif 'tcfd' in source_value:
                        source = MetricSource.TCFD
                    elif 'ungc' in source_value:
                        source = MetricSource.UNGC
                    else:
                        source = MetricSource.CUSTOM
                    
                    # 处理关键词
                    keywords_str = str(row.get(actual_columns.get('keywords', ''), ''))
                    if keywords_str and keywords_str != 'nan':
                        keywords = [k.strip() for k in keywords_str.split(',') if k.strip()]
                    else:
                        keywords = []
                    
                    # 其他字段
                    description = str(row.get(actual_columns.get('description', ''), ''))
                    definition = str(row.get(actual_columns.get('definition', ''), ''))
                    unit = str(row.get(actual_columns.get('unit', ''), ''))
                    
                    # 创建去重键（基于指标名称和代码）
                    dedup_key = f"{metric_name}||{metric_code}".lower().strip()
                    
                    # 检查是否重复
                    if dedup_key in seen_metrics:
                        logger.warning(f"跳过重复指标: {metric_name} ({metric_code}) 在第 {index+1} 行")
                        continue
                    
                    seen_metrics.add(dedup_key)
                    
                    # 创建指标对象
                    metric = ESGMetric(
                        metric_id=f"metric_{index+1:03d}",
                        metric_name=metric_name,
                        metric_code=metric_code,
                        category=category,
                        source=source,
                        keywords=keywords,
                        description=description if description != 'nan' else '',
                        definition=definition if definition != 'nan' else '',
                        unit=unit if unit != 'nan' else None
                    )
                    metrics.append(metric)
                    
                except Exception as e:
                    logger.warning(f"跳过第 {index+1} 行，解析错误: {str(e)}")
                    continue
            
            collection = MetricCollection(
                collection_id=f"excel_collection_{uuid.uuid4().hex[:8]}",
                collection_name=f"从Excel加载的指标集合 - {file_path.name}",
                metrics=metrics
            )
            
            logger.info(f"成功从Excel加载 {len(metrics)} 个指标")
            return collection
            
        except Exception as e:
            logger.error(f"Excel文件加载失败: {str(e)}")
            raise ESGEncodingError(f"Excel文件加载失败: {str(e)}")
    
    def load_sasb_metrics_by_industry(self, semi_industry: str) -> MetricCollection:
        """
        根据细分行业加载SASB指标
        
        Args:
            semi_industry: 细分行业名称
            
        Returns:
            MetricCollection: SASB指标集合
        """
        try:
            logger.info(f"load_sasb_metrics_by_industry called with semi_industry: {semi_industry} (type: {type(semi_industry)})")
            if semi_industry is None:
                raise ValueError("semi_industry parameter is required and cannot be None")
            industry_file_mapping = _load_sasb_industry_file_mapping()

            if semi_industry not in industry_file_mapping:
                # Runtime-safe refresh: manifest may have been updated while backend is running.
                industry_file_mapping = _reload_sasb_industry_file_mapping()

            if semi_industry not in industry_file_mapping:
                raise ValueError(
                    f"Unsupported industry: {semi_industry}. "
                    f"Supported industries: {list(industry_file_mapping.keys())}"
                )

            file_path = _SASB_METRICS_DIR / industry_file_mapping[semi_industry]
            
            if not file_path.exists():
                raise FileNotFoundError(f"SASB metrics file not found: {file_path}. Please ensure the metrics data file exists.")
            
            # 读取SASB指标数据
            logger.info(f"Reading SASB file: {file_path}")
            with open(file_path, 'r', encoding='utf-8') as f:
                sasb_data = json.load(f)
            
            logger.info(f"Loaded {len(sasb_data)} SASB items from file")
            
            # 转换为ESGMetric对象
            metrics = []
            for i, item in enumerate(sasb_data):
                # 兼容不同的JSON格式（大写Metric和小写metric）
                metric_name_raw = item.get('Metric') or item.get('metric') or f'Unknown Metric {i}'
                logger.info(f"Processing SASB item {i+1}/{len(sasb_data)}: {metric_name_raw[:50]}...")
                
                # 确定类别 - 兼容大小写
                # 确保topic是字符串类型，处理可能的float/NaN值
                topic_raw = item.get('Topic') or item.get('topic') or ''
                if topic_raw is None:
                    topic = ''
                elif isinstance(topic_raw, float):
                    # 处理NaN和无穷大值
                    if math.isnan(topic_raw) or math.isinf(topic_raw):
                        topic = ''
                    else:
                        topic = str(topic_raw)
                else:
                    topic = str(topic_raw)
                category = self._determine_metric_category(topic)
                
                logger.info(f"  - Extracting keywords for item {i+1}...")
                keywords = self._extract_keywords_from_sasb(item)
                logger.info(f"  - Keywords extracted: {len(keywords)} keywords")
                
                # Use metric name as unique ID since the same code can have multiple metrics
                metric_name = metric_name_raw
                # Create a unique ID from the metric name (clean it up for use as ID)
                metric_id = f"sasb_{i}_{metric_name[:50].replace(' ', '_').replace('(', '').replace(')', '').replace(',', '')}"
                
                # 兼容大小写的字段获取
                metric_code = item.get('Code') or item.get('code') or ''
                #print(metric_code)
                
                # 处理unit字段，确保是字符串或None，处理可能的float/NaN值
                unit_raw = item.get('Unit') or item.get('unit') or ''
                if unit_raw is None:
                    unit = None
                elif isinstance(unit_raw, float):
                    # 处理NaN和无穷大值
                    if math.isnan(unit_raw) or math.isinf(unit_raw):
                        unit = None
                    else:
                        unit = str(unit_raw) if unit_raw else None
                else:
                    unit = str(unit_raw) if unit_raw else None
                
                # 处理sasb_category字段
                sasb_category_raw = item.get('Category') or item.get('category') or ''
                if sasb_category_raw is None:
                    sasb_category = ''
                elif isinstance(sasb_category_raw, float):
                    if math.isnan(sasb_category_raw) or math.isinf(sasb_category_raw):
                        sasb_category = ''
                    else:
                        sasb_category = str(sasb_category_raw)
                else:
                    sasb_category = str(sasb_category_raw)
                
                # 处理sasb_type字段
                sasb_type_raw = item.get('Type') or item.get('type') or ''
                if sasb_type_raw is None:
                    sasb_type = ''
                elif isinstance(sasb_type_raw, float):
                    if math.isnan(sasb_type_raw) or math.isinf(sasb_type_raw):
                        sasb_type = ''
                    else:
                        sasb_type = str(sasb_type_raw)
                else:
                    sasb_type = str(sasb_type_raw)
                
                metric = ESGMetric(
                    metric_id=metric_id,
                    metric_name=metric_name,
                    metric_code=metric_code,  # Keep original code unchanged
                    category=category,
                    source=MetricSource.SASB,
                    keywords=keywords,
                    description=f"{topic}: {metric_name}" if topic else metric_name,
                    definition=self._extract_definition_text(item),
                    unit=unit,
                    # Save original SASB fields for display - 兼容大小写
                    sasb_category=sasb_category,
                    sasb_type=sasb_type,
                    sasb_topic=topic or None  # Allow None for Activity Metrics
                )
                metrics.append(metric)
                logger.info(f"  - Metric {i+1} created successfully")
            
            collection = MetricCollection(
                collection_id=f"sasb_{semi_industry.lower().replace(' ', '_')}_{uuid.uuid4().hex[:8]}",
                collection_name=f"SASB Metrics for {semi_industry}",
                metrics=metrics
            )
            
            logger.info(f"Loaded {len(metrics)} SASB metrics for industry: {semi_industry}")
            return collection
            
        except Exception as e:
            logger.error(f"Error loading SASB metrics for {semi_industry}: {e}")
            raise RuntimeError(f"Failed to load SASB metrics for industry '{semi_industry}': {e}")
    
    def load_gri_metrics_by_sector_topic(self, sector_slug: str, topic_slug: str) -> MetricCollection:
        """
        Load GRI metrics for a single sector-topic from backend/data/gri_metrics.
        File naming: {sector_slug}_{topic_slug}.json (e.g. coal_sector_climate_change.json).
        
        Args:
            sector_slug: Sector slug (e.g. coal_sector, oil_and_gas_sector)
            topic_slug: Topic slug (e.g. climate_change, biodiversity)
            
        Returns:
            MetricCollection: GRI metrics for that sector-topic
        """
        try:
            if not sector_slug or not topic_slug:
                raise ValueError("GRI sector_slug and topic_slug are required")
            gri_dir = Path(__file__).parent.parent.parent / "data" / "gri_metrics"
            file_name = f"{sector_slug.strip()}_{topic_slug.strip()}.json"
            file_path = gri_dir / file_name
            if not file_path.exists():
                raise FileNotFoundError(
                    f"GRI metrics file not found: {file_path}. "
                    f"Expected file naming: {{sector_slug}}_{{topic_slug}}.json"
                )
            with open(file_path, "r", encoding="utf-8") as f:
                gri_data = json.load(f)
            if not isinstance(gri_data, list):
                raise ValueError(f"GRI file must be a JSON array: {file_path}")
            metrics = []
            for i, item in enumerate(gri_data):
                metric_name = item.get("Metric") or item.get("metric") or f"GRI metric {i+1}"
                topic_raw = item.get("Topic") or item.get("topic") or ""
                if topic_raw is None:
                    topic = ""
                elif isinstance(topic_raw, float):
                    topic = "" if (math.isnan(topic_raw) or math.isinf(topic_raw)) else str(topic_raw)
                else:
                    topic = str(topic_raw)
                code = item.get("Code") or item.get("code") or ""
                type_str = item.get("Type") or item.get("type") or ""
                category = self._determine_metric_category(topic)
                keywords = self._extract_keywords_from_sasb(item)  # same shape as GRI (Metric, Topic)
                metric_id = f"gri_{i}_{metric_name[:50].replace(' ', '_').replace('(', '').replace(')', '').replace(',', '')}"
                metric = ESGMetric(
                    metric_id=metric_id,
                    metric_name=metric_name,
                    metric_code=code,
                    category=category,
                    source=MetricSource.GRI,
                    keywords=keywords,
                    description=f"{topic}: {metric_name}" if topic else metric_name,
                    definition=self._extract_definition_text(item),
                    unit=None,
                    sasb_category="",
                    sasb_type=type_str if type_str else "",
                    sasb_topic=topic or None,
                )
                metrics.append(metric)
            collection = MetricCollection(
                collection_id=f"gri_{sector_slug}_{topic_slug}_{uuid.uuid4().hex[:8]}",
                collection_name=f"GRI Metrics — {sector_slug} / {topic_slug}",
                metrics=metrics,
            )
            logger.info(f"Loaded {len(metrics)} GRI metrics for {sector_slug} / {topic_slug}")
            return collection
        except Exception as e:
            logger.error(f"Error loading GRI metrics for {sector_slug}/{topic_slug}: {e}")
            raise RuntimeError(f"Failed to load GRI metrics: {e}") from e

    def load_cdp_metrics_by_topic(self, topic_slug: str) -> MetricCollection:
        """
        Load CDP metrics from backend/data/cdp_metrics/{topic_slug}.json (same item shape as SASB JSON).
        """
        try:
            if not topic_slug or not str(topic_slug).strip():
                raise ValueError("CDP topic slug is required")
            slug = str(topic_slug).strip()
            if slug not in _CDP_TOPIC_SLUGS:
                raise ValueError(
                    f"Unsupported CDP topic: {slug}. Supported: {sorted(_CDP_TOPIC_SLUGS)}"
                )
            file_path = _CDP_METRICS_DIR / f"{slug}.json"
            if not file_path.exists():
                raise FileNotFoundError(f"CDP metrics file not found: {file_path}")
            with open(file_path, "r", encoding="utf-8") as f:
                cdp_data = json.load(f)
            if not isinstance(cdp_data, list):
                raise ValueError(f"CDP file must be a JSON array: {file_path}")
            metrics = []
            for i, item in enumerate(cdp_data):
                metric_name_raw = item.get("Metric") or item.get("metric") or f"CDP metric {i+1}"
                topic_raw = item.get("Topic") or item.get("topic") or ""
                if topic_raw is None:
                    topic = ""
                elif isinstance(topic_raw, float):
                    topic = "" if (math.isnan(topic_raw) or math.isinf(topic_raw)) else str(topic_raw)
                else:
                    topic = str(topic_raw)
                category = self._determine_metric_category(topic)
                keywords = self._extract_keywords_from_sasb(item)
                metric_code = item.get("Code") or item.get("code") or ""
                unit_raw = item.get("Unit") or item.get("unit") or ""
                if unit_raw is None or unit_raw == "":
                    unit = None
                elif isinstance(unit_raw, float):
                    unit = None if (math.isnan(unit_raw) or math.isinf(unit_raw)) else str(unit_raw)
                else:
                    unit = str(unit_raw) if unit_raw else None
                sasb_category_raw = item.get("Category") or item.get("category") or ""
                sasb_category = (
                    ""
                    if sasb_category_raw is None
                    else (
                        ""
                        if isinstance(sasb_category_raw, float)
                        and (math.isnan(sasb_category_raw) or math.isinf(sasb_category_raw))
                        else str(sasb_category_raw)
                    )
                )
                sasb_type_raw = item.get("Type") or item.get("type") or ""
                sasb_type = (
                    ""
                    if sasb_type_raw is None
                    else (
                        ""
                        if isinstance(sasb_type_raw, float)
                        and (math.isnan(sasb_type_raw) or math.isinf(sasb_type_raw))
                        else str(sasb_type_raw)
                    )
                )
                metric_id = (
                    f"cdp_{i}_{metric_name_raw[:50].replace(' ', '_').replace('(', '').replace(')', '').replace(',', '')}"
                )
                metric = ESGMetric(
                    metric_id=metric_id,
                    metric_name=metric_name_raw,
                    metric_code=str(metric_code) if metric_code is not None else "",
                    category=category,
                    source=MetricSource.CDP,
                    keywords=keywords,
                    description=f"{topic}: {metric_name_raw}" if topic else str(metric_name_raw),
                    definition=self._extract_definition_text(item),
                    unit=unit,
                    sasb_category=sasb_category,
                    sasb_type=sasb_type,
                    sasb_topic=topic or None,
                )
                metrics.append(metric)
            collection = MetricCollection(
                collection_id=f"cdp_{slug.lower()}_{uuid.uuid4().hex[:8]}",
                collection_name=f"CDP Metrics — {slug}",
                metrics=metrics,
            )
            logger.info(f"Loaded {len(metrics)} CDP metrics for topic: {slug}")
            return collection
        except Exception as e:
            logger.error(f"Error loading CDP metrics for {topic_slug}: {e}")
            raise RuntimeError(f"Failed to load CDP metrics: {e}") from e

    def load_tcfd_metrics_by_topic(self, topic_slug: str) -> MetricCollection:
        """
        Load TCFD metrics from backend/data/tcfd_metrics/{topic_slug}.json (SASB-like item shape).
        """
        try:
            if not topic_slug or not str(topic_slug).strip():
                raise ValueError("TCFD topic slug is required")
            slug = str(topic_slug).strip()
            if slug not in _TCFD_TOPIC_SLUGS:
                raise ValueError(
                    f"Unsupported TCFD topic: {slug}. Supported: {sorted(_TCFD_TOPIC_SLUGS)}"
                )
            file_path = _TCFD_METRICS_DIR / f"{slug}.json"
            if not file_path.exists():
                raise FileNotFoundError(f"TCFD metrics file not found: {file_path}")
            with open(file_path, "r", encoding="utf-8") as f:
                tcfd_data = json.load(f)
            if not isinstance(tcfd_data, list):
                raise ValueError(f"TCFD file must be a JSON array: {file_path}")
            metrics = []
            for i, item in enumerate(tcfd_data):
                metric_name_raw = item.get("Metric") or item.get("metric") or f"TCFD metric {i+1}"
                topic_raw = item.get("Topic") or item.get("topic") or ""
                if topic_raw is None:
                    topic = ""
                elif isinstance(topic_raw, float):
                    topic = "" if (math.isnan(topic_raw) or math.isinf(topic_raw)) else str(topic_raw)
                else:
                    topic = str(topic_raw)
                category = self._determine_metric_category(topic)
                keywords = self._extract_keywords_from_sasb(item)
                metric_code = item.get("Code") or item.get("code") or ""
                unit_raw = item.get("Unit") or item.get("unit") or ""
                if unit_raw is None or unit_raw == "":
                    unit = None
                elif isinstance(unit_raw, float):
                    unit = None if (math.isnan(unit_raw) or math.isinf(unit_raw)) else str(unit_raw)
                else:
                    unit = str(unit_raw) if unit_raw else None
                sasb_category_raw = item.get("Category") or item.get("category") or ""
                sasb_category = (
                    ""
                    if sasb_category_raw is None
                    else (
                        ""
                        if isinstance(sasb_category_raw, float)
                        and (math.isnan(sasb_category_raw) or math.isinf(sasb_category_raw))
                        else str(sasb_category_raw)
                    )
                )
                sasb_type_raw = item.get("Type") or item.get("type") or ""
                sasb_type = (
                    ""
                    if sasb_type_raw is None
                    else (
                        ""
                        if isinstance(sasb_type_raw, float)
                        and (math.isnan(sasb_type_raw) or math.isinf(sasb_type_raw))
                        else str(sasb_type_raw)
                    )
                )
                metric_id = (
                    f"tcfd_{i}_{metric_name_raw[:50].replace(' ', '_').replace('(', '').replace(')', '').replace(',', '')}"
                )
                metric = ESGMetric(
                    metric_id=metric_id,
                    metric_name=metric_name_raw,
                    metric_code=str(metric_code) if metric_code is not None else "",
                    category=category,
                    source=MetricSource.TCFD,
                    keywords=keywords,
                    description=f"{topic}: {metric_name_raw}" if topic else str(metric_name_raw),
                    definition=self._extract_definition_text(item),
                    unit=unit,
                    sasb_category=sasb_category,
                    sasb_type=sasb_type,
                    sasb_topic=topic or None,
                )
                metrics.append(metric)
            collection = MetricCollection(
                collection_id=f"tcfd_{slug.lower()}_{uuid.uuid4().hex[:8]}",
                collection_name=f"TCFD Metrics — {slug}",
                metrics=metrics,
            )
            logger.info(f"Loaded {len(metrics)} TCFD metrics for topic: {slug}")
            return collection
        except Exception as e:
            logger.error(f"Error loading TCFD metrics for {topic_slug}: {e}")
            raise RuntimeError(f"Failed to load TCFD metrics: {e}") from e


    def _extract_definition_text(self, item: dict) -> str:
        """Extract framework definition text exactly as provided without fallback to other fields."""
        raw = item.get("definition")
        if raw is None:
            raw = item.get("Definition")
        if raw is None:
            return ""
        if isinstance(raw, float):
            if math.isnan(raw) or math.isinf(raw):
                return ""
        text = str(raw).strip()
        return "" if text == "nan" else text

    def _determine_metric_category(self, topic: str) -> MetricCategory:
        """
        根据主题确定指标类别
        
        Args:
            topic: SASB主题
            
        Returns:
            MetricCategory: 指标类别
        """
        # 确保topic是字符串类型，处理可能的float/NaN值
        if topic is None:
            topic_str = ''
        elif isinstance(topic, float):
            # 处理NaN和无穷大值
            if math.isnan(topic) or math.isinf(topic):
                topic_str = ''
            else:
                topic_str = str(topic)
        else:
            topic_str = str(topic)
        topic_lower = topic_str.lower()
        
        if any(keyword in topic_lower for keyword in ['environmental', 'energy', 'emissions', 'waste', 'water', 'climate']):
            return MetricCategory.ENVIRONMENTAL
        elif any(keyword in topic_lower for keyword in ['employee', 'labor', 'diversity', 'human', 'safety', 'community']):
            return MetricCategory.SOCIAL  
        elif any(keyword in topic_lower for keyword in ['governance', 'ethics', 'compliance', 'risk', 'board', 'audit']):
            return MetricCategory.GOVERNANCE
        else:
            # 默认为治理类别
            return MetricCategory.GOVERNANCE
    
    def _extract_keywords_from_sasb(self, sasb_item: dict) -> List[str]:
        """
        从SASB指标项中提取关键词
        
        Args:
            sasb_item: SASB指标数据项
            
        Returns:
            List[str]: 关键词列表
        """
        metric = self._safe_metric_text(sasb_item.get('Metric') or sasb_item.get('metric') or '')
        topic = self._safe_metric_text(sasb_item.get('Topic') or sasb_item.get('topic') or '')
        code = self._safe_metric_text(sasb_item.get('Code') or sasb_item.get('code') or '')
        unit = self._safe_metric_text(sasb_item.get('Unit') or sasb_item.get('unit') or '')
        definition = self._extract_definition_text(sasb_item)
        return self._build_retrieval_keywords_from_fields(
            metric_name=metric,
            metric_code=code,
            topic=topic,
            unit=unit,
            definition=definition,
        )

    def _safe_metric_text(self, value: Union[str, float, int, None]) -> str:
        """Safely convert framework field values to normalized text."""
        if value is None:
            return ""
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return ""
        text = str(value).strip()
        return "" if text.lower() == "nan" else text

    def _tokenize_retrieval_text(self, text: str) -> List[str]:
        """Tokenize text for retrieval keywords while filtering common stop words."""
        stop_words = {
            'and', 'or', 'the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'to', 'for', 'in',
            'on', 'at', 'by', 'with', 'from', 'as', 'that', 'this', 'these', 'those', 'be', 'been',
            'being', 'into', 'within', 'during', 'under', 'over', 'per', 'each', 'total', 'number'
        }
        if not text:
            return []
        parts = re.split(r'[^A-Za-z0-9%./+-]+', text.lower())
        tokens: List[str] = []
        for part in parts:
            token = part.strip('().,;:[]{}')
            if len(token) <= 2:
                continue
            if token in stop_words:
                continue
            tokens.append(token)
        return tokens

    def _extract_definition_keywords(self, definition: str, limit: int = 16) -> List[str]:
        """Extract stable lexical hints from metric definitions for retrieval."""
        if not definition:
            return []
        keywords: List[str] = []
        for chunk in re.split(r'[\n;:,.]', definition):
            chunk = chunk.strip()
            if not chunk:
                continue
            lowered = chunk.lower()
            if 3 <= len(lowered) <= 80:
                keywords.append(lowered)
            keywords.extend(self._tokenize_retrieval_text(chunk))
        deduped = list(OrderedDict.fromkeys(k for k in keywords if k))
        return deduped[:limit]

    def _build_retrieval_keywords_from_fields(
        self,
        metric_name: str,
        metric_code: str,
        topic: str,
        unit: str,
        definition: str,
    ) -> List[str]:
        """Build lexical retrieval keywords from framework fields actually used by analysis."""
        keywords: List[str] = []
        exact_phrases = [metric_name, metric_code, topic, unit]
        for phrase in exact_phrases:
            phrase = self._safe_metric_text(phrase)
            if not phrase:
                continue
            keywords.append(phrase.lower())
            keywords.extend(self._tokenize_retrieval_text(phrase))
        keywords.extend(self._extract_definition_keywords(definition))
        deduped = list(OrderedDict.fromkeys(k for k in keywords if k))
        return deduped[:32]

    def _build_metric_retrieval_keywords(self, metric: ESGMetric) -> List[str]:
        """Merge existing keywords with Metric / Code / Topic / Unit / definition lexical hints."""
        keywords: List[str] = []
        keywords.extend(metric.keywords or [])
        keywords.extend(
            self._build_retrieval_keywords_from_fields(
                metric_name=metric.metric_name,
                metric_code=metric.metric_code,
                topic=metric.sasb_topic or '',
                unit=metric.unit or '',
                definition=metric.definition or '',
            )
        )
        deduped = list(OrderedDict.fromkeys(k.strip() for k in keywords if str(k).strip()))
        return deduped[:40]

    def _build_semantic_query_text(self, metric: ESGMetric) -> str:
        """Build a deterministic semantic query for semantic retrieval without mixing Code / Topic / Unit."""
        parts = [f"Metric: {metric.metric_name}"]

        description = self._safe_metric_text(metric.description)
        if description:
            parts.append(f"Description: {description}")

        definition = self._safe_metric_text(metric.definition)
        if definition:
            parts.append(f"Definition: {definition}")

        semantic_keywords = list(OrderedDict.fromkeys(
            self._tokenize_retrieval_text(metric.metric_name)
            + self._tokenize_retrieval_text(description)
            + self._extract_definition_keywords(definition, limit=20)
        ))
        if semantic_keywords:
            parts.append(f"Keywords: {', '.join(semantic_keywords[:24])}")

        return "\n".join(parts)
    
    def generate_semantic_description(self, metric: ESGMetric) -> str:
        """
        使用LLM为指标生成语义描述

        Args:
            metric: ESG指标

        Returns:
            str: 语义描述
        """
        base_query = self._build_semantic_query_text(metric)
        if not self.llm_client:
            logger.info(f"LLM client unavailable, using deterministic semantic query for metric {metric.metric_id}")
            return base_query
        try:
            semantic_keywords = list(OrderedDict.fromkeys(
                self._tokenize_retrieval_text(metric.metric_name)
                + self._tokenize_retrieval_text(metric.description or "")
                + self._extract_definition_keywords(metric.definition or "", limit=20)
            ))

            prompt = f"""
            请基于以下ESG指标信息生成一个详细的语义检索描述，用于在报告中召回最相关的披露证据。

            注意：不要结合或引用指标代码、主题、单位，只围绕指标名称、已有描述和definition原文来组织语义描述与扩展。

            指标名称: {metric.metric_name}
            类别: {metric.category}
            来源: {metric.source}
            关键词: {', '.join(semantic_keywords[:24])}
            描述: {metric.description}
            定义: {metric.definition or '无'}

            请生成一个100-200字的语义描述，包含：
            1. 指标的核心含义
            2. 相关的业务场景
            3. 可能的同义词或相关概念
            4. 在ESG报告中的典型表达方式

            不要输出指标代码、主题名称、单位字段，不要单独总结单位、范围或主题限定。
            请用中文回复，不要包含任何格式标记。
            """

            response = self.llm_client.chat.completions.create(
                model=self.config.llm_model,
                messages=[
                    {"role": "system", "content": "你是一位ESG专家，负责为ESG指标生成准确的语义描述。"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=300,
                temperature=1 # CHANGE TO 1 FOR GPT-5
            )

            description = response.choices[0].message.content.strip()
            logger.info(f"为指标 {metric.metric_id} 生成语义描述")
            return f"{base_query}\n\n语义扩展:\n{description}" if description else base_query

        except Exception as e:
            logger.warning(f"LLM semantic description generation failed, fallback to deterministic semantic query: {str(e)}")
            return base_query
    
    def expand_metric_semantics(self, metric: ESGMetric) -> SemanticExpansion:
        """
        为指标进行语义扩展
        
        Args:
            metric: ESG指标
            
        Returns:
            SemanticExpansion: 语义扩展结果
        """
        try:
            # 生成语义描述
            semantic_description = self.generate_semantic_description(metric)
            
            # 扩展关键词
            semantic_keyword_seed = list(OrderedDict.fromkeys(
                self._tokenize_retrieval_text(metric.metric_name)
                + self._tokenize_retrieval_text(metric.description or "")
                + self._extract_definition_keywords(metric.definition or "", limit=20)
            ))
            expanded_keywords = self._expand_keywords(semantic_keyword_seed, semantic_description)
            
            # 生成嵌入向量
            embedding_model = self._ensure_embedding_model()
            embedding = encode_query_texts(
                embedding_model,
                [semantic_description],
                convert_to_tensor=False,
            )[0].tolist()
            
            expansion = SemanticExpansion(
                metric_id=metric.metric_id,
                semantic_description=semantic_description,
                expanded_keywords=expanded_keywords,
                context_information=f"类别: {metric.category.value}, 来源: {metric.source.value}",
                embedding=embedding
            )
            
            logger.info(f"完成指标 {metric.metric_id} 的语义扩展")
            return expansion
            
        except Exception as e:
            logger.error(f"指标语义扩展失败: {str(e)}")
            raise ESGEncodingError(f"指标语义扩展失败: {str(e)}")
    
    def _expand_keywords(self, original_keywords: List[str], semantic_description: str) -> List[str]:
        """
        扩展关键词
        
        Args:
            original_keywords: 原始关键词
            semantic_description: 语义描述
            
        Returns:
            List[str]: 扩展后的关键词
        """
        expanded = set(original_keywords)
        
        # 基于语义描述提取额外关键词
        # 这里可以使用更复杂的NLP技术，目前使用简单的规则
        description_words = semantic_description.split()
        
        # 添加一些相关词
        for word in description_words:
            if len(word) > 1 and word not in expanded:
                expanded.add(word)
        
        return list(expanded)
    
    def process_metric_collection(self, collection: MetricCollection) -> MetricCollection:
        """
        处理整个指标集合，进行语义扩展
        
        Args:
            collection: 指标集合
            
        Returns:
            MetricCollection: 处理后的指标集合
        """
        try:
            logger.info(f"开始处理指标集合: {collection.collection_name}")

            semantic_expansions = []
            enriched_metrics = []
            existing_expansions = {
                exp.metric_id: exp for exp in (collection.semantic_expansions or []) if exp.metric_id
            }

            for metric in collection.metrics:
                logger.info(f"正在处理指标: {metric.metric_name}")
                enriched_metric = metric.copy(deep=True)
                enriched_metric.keywords = self._build_metric_retrieval_keywords(enriched_metric)
                enriched_metrics.append(enriched_metric)

                existing_expansion = existing_expansions.get(enriched_metric.metric_id)
                if existing_expansion and existing_expansion.embedding and existing_expansion.semantic_description:
                    semantic_keyword_seed = list(OrderedDict.fromkeys(
                        self._tokenize_retrieval_text(enriched_metric.metric_name)
                        + self._tokenize_retrieval_text(enriched_metric.description or "")
                        + self._extract_definition_keywords(enriched_metric.definition or "", limit=20)
                    ))
                    existing_expansion.expanded_keywords = self._expand_keywords(
                        semantic_keyword_seed,
                        existing_expansion.semantic_description,
                    )
                    semantic_expansions.append(existing_expansion)
                    continue

                expansion = self.expand_metric_semantics(enriched_metric)
                semantic_expansions.append(expansion)

            # 更新集合
            collection.metrics = enriched_metrics
            collection.semantic_expansions = semantic_expansions

            logger.info(f"成功处理 {len(semantic_expansions)} 个指标的语义扩展")
            return collection

        except Exception as e:
            logger.error(f"处理指标集合失败: {str(e)}")
            raise ESGEncodingError(f"处理指标集合失败: {str(e)}")
    
    def save_metric_collection(self, collection: MetricCollection, file_path: Union[str, Path]):
        """
        保存指标集合到文件
        
        Args:
            collection: 指标集合
            file_path: 保存路径
        """
        try:
            file_path = Path(file_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 转换为字典格式，处理日期时间序列化
            data = {
                "collection_id": collection.collection_id,
                "collection_name": collection.collection_name,
                "created_at": collection.created_at.isoformat(),
                "metrics": [],
                "semantic_expansions": []
            }
            
            # 处理指标数据
            for metric in collection.metrics:
                metric_dict = metric.dict()
                metric_dict["created_at"] = metric.created_at.isoformat()
                data["metrics"].append(metric_dict)
            
            # 处理语义扩展数据
            for expansion in collection.semantic_expansions:
                expansion_dict = expansion.dict()
                expansion_dict["created_at"] = expansion.created_at.isoformat()
                data["semantic_expansions"].append(expansion_dict)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"指标集合已保存到: {file_path}")
            
        except Exception as e:
            logger.error(f"保存指标集合失败: {str(e)}")
            raise ESGEncodingError(f"保存指标集合失败: {str(e)}") 
