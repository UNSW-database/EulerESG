"""
Standards-Based Metric Extraction & Expansion 模块

该模块负责：
1. Standard Metric Identification (SMI) - 标准指标识别
2. Standard-Aligned Metrics Refinement - 标准对齐指标精炼
3. Semantic Expansion of Metric Definitions - 指标定义的语义扩展
"""

import json
import uuid
from typing import Dict, List, Optional, Union
from datetime import datetime
from pathlib import Path
from loguru import logger
import openai
import pandas as pd
from sentence_transformers import SentenceTransformer

from .models import (
    ESGMetric, MetricCategory, MetricSource, SemanticExpansion, 
    MetricCollection, ProcessingConfig
)
from .exceptions import ESGEncodingError, ContentEmbeddingError


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
        
        # 初始化嵌入模型
        self._init_embedding_model()
        
        # 初始化LLM客户端
        if config.llm_api_key:
            self._init_llm_client()
    
    def _init_embedding_model(self):
        """初始化嵌入模型"""
        try:
            logger.info(f"正在加载嵌入模型: {self.config.embedding_model}")
            self.embedding_model = SentenceTransformer(
                self.config.embedding_model,
                device=self.config.device
            )
            logger.info("嵌入模型加载成功")
        except Exception as e:
            logger.error(f"嵌入模型加载失败: {str(e)}")
            raise ContentEmbeddingError(f"嵌入模型加载失败: {str(e)}")
    
    def _init_llm_client(self):
        """初始化LLM客户端"""
        if not self.config.llm_api_key:
            raise ValueError("LLM API key is required for metric processing. Please configure LLM_API_KEY in your .env file.")

        base_url = self.config.llm_base_url or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

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
                'description': ['Context', 'description', 'desc', '描述', 'definition'],
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
            # 行业名称到文件名的映射
            industry_file_mapping = {
                "Software & IT Services": "software_&_it_services.json",
                "Hardware": "Hardware.json",
                "Semiconductors": "semiconductors.json",
                "Internet Media & Services": "Internet_Media_and_Services.json",
                "Telecommunication Services": "telecommunication_services.json",
                "Electronic Manufacturing Services & Original Design Manufacturing": "Electronic_Manufacturing_Servic.json",
              
                # MCG Financials metrics (now in sasb_metrics directory)
                "Investment Banking & Brokerage": "investment_banking_brokerage.json",
                "Commercial Banks": "commercial_banks.json",
                "Asset Management & Custody Activities": "asset_management_custody.json",
                "E-Commerce": "e-commerce.json",

                # P&G Consumers metrics (patch)
                "Apparel, Accessories & Footwear": "apparel_accessories_&_footwear.json",
                "Household & Personal Products": "household_&_personal_products.json",
                "Multiline and Specialty Retailers & Distributors": "multiline_and_specialty_retailers_&_distributors.json",

                # BMW Transportation metrics (patch)
                "Automobiles": "automobiles.json",
                "Auto Parts": "auto_parts.json",
                "Car Rental & Leasing": "car_rental_&_leasing.json",
            }
            
            if semi_industry not in industry_file_mapping:
                raise ValueError(f"Unsupported industry: {semi_industry}. Supported industries: {list(industry_file_mapping.keys())}")
            
            # 构建文件路径 - 统一使用sasb_metrics目录
            file_path = Path(__file__).parent.parent.parent / "data" / "sasb_metrics" / industry_file_mapping[semi_industry]
            
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
                topic = item.get('Topic') or item.get('topic') or ''
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
                
                metric = ESGMetric(
                    metric_id=metric_id,
                    metric_name=metric_name,
                    metric_code=metric_code,  # Keep original code unchanged
                    category=category,
                    source=MetricSource.SASB,
                    keywords=keywords,
                    description=f"{topic}: {metric_name}" if topic else metric_name,
                    unit=item.get('Unit') or item.get('unit') or '',
                    # Save original SASB fields for display - 兼容大小写
                    sasb_category=item.get('Category') or item.get('category') or '',
                    sasb_type=item.get('Type') or item.get('type') or '',
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
    
    def _determine_metric_category(self, topic: str) -> MetricCategory:
        """
        根据主题确定指标类别
        
        Args:
            topic: SASB主题
            
        Returns:
            MetricCategory: 指标类别
        """
        topic_lower = (topic or '').lower()
        
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
        keywords = []
        
        # 从指标名称提取关键词 - 兼容大小写字段名
        metric = (sasb_item.get('Metric') or sasb_item.get('metric') or '').lower()
        topic = (sasb_item.get('Topic') or sasb_item.get('topic') or '').lower()
        
        # 简单的关键词提取（去除常见词汇）
        stop_words = {'and', 'or', 'the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'to', 'for', 'in', 'on', 'at', 'by'}
        
        for text in [metric, topic]:
            words = [word.strip('().,;:') for word in text.split() if len(word.strip('().,;:')) > 2]
            keywords.extend([word for word in words if word not in stop_words])
        
        # 去重并限制数量
        return list(set(keywords))[:10]
    
    def generate_semantic_description(self, metric: ESGMetric) -> str:
        """
        使用LLM为指标生成语义描述

        Args:
            metric: ESG指标

        Returns:
            str: 语义描述
        """
        try:
            prompt = f"""
            请为以下ESG指标生成一个详细的语义描述，用于向量检索匹配：

            指标名称: {metric.metric_name}
            指标代码: {metric.metric_code}
            类别: {metric.category}
            来源: {metric.source}
            关键词: {', '.join(metric.keywords)}
            描述: {metric.description}
            单位: {metric.unit or '无'}

            请生成一个100-200字的语义描述，包含：
            1. 指标的核心含义
            2. 相关的业务场景
            3. 可能的同义词或相关概念
            4. 在ESG报告中的典型表达方式

            请用中文回复，不要包含任何格式标记。
            """
            
            response = self.llm_client.chat.completions.create(
                model=self.config.llm_model,
                messages=[
                    {"role": "system", "content": "你是一位ESG专家，负责为ESG指标生成准确的语义描述。"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=300,
                temperature=0.3 # CHANGE TO 1 FOR GPT-5
            )
            
            description = response.choices[0].message.content.strip()
            logger.info(f"为指标 {metric.metric_id} 生成语义描述")
            return description
            
        except Exception as e:
            logger.error(f"LLM semantic description generation failed: {str(e)}")
            raise RuntimeError(f"Failed to generate semantic description for metric {metric.metric_id}: {e}")
    
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
            expanded_keywords = self._expand_keywords(metric.keywords, semantic_description)
            
            # 生成嵌入向量
            embedding = self.embedding_model.encode(
                semantic_description, 
                convert_to_tensor=False
            ).tolist()
            
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
            
            for metric in collection.metrics:
                logger.info(f"正在处理指标: {metric.metric_name}")
                expansion = self.expand_metric_semantics(metric)
                semantic_expansions.append(expansion)
            
            # 更新集合
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
