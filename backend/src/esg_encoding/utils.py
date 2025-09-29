"""
ESG报告编码的工具函数

提供通用的工具函数和辅助方法。
"""

import re
import hashlib
import mimetypes
from typing import Dict, List, Optional, Tuple, Any, Union
from pathlib import Path
from datetime import datetime
from uuid import uuid4

import numpy as np
import pandas as pd
from loguru import logger

from .exceptions import FileFormatError, ValidationError


def generate_segment_id(content: str, page_number: int, position: Optional[Dict[str, float]] = None) -> str:
    """
    生成段落的唯一标识符
    
    Args:
        content: 段落内容
        page_number: 页码
        position: 位置信息
    
    Returns:
        唯一的段落ID
    """
    # 基于内容和位置生成哈希
    content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()[:8]
    
    if position:
        pos_str = f"{position.get('x', 0):.1f}_{position.get('y', 0):.1f}"
    else:
        pos_str = "0.0_0.0"
    
    return f"seg_{page_number}_{pos_str}_{content_hash}"


def generate_document_id(file_path: Union[str, Path]) -> str:
    """
    生成文档的唯一标识符
    
    Args:
        file_path: 文件路径
    
    Returns:
        唯一的文档ID
    """
    file_path = Path(file_path)
    
    # 基于文件路径和名称生成哈希
    path_hash = hashlib.md5(str(file_path).encode('utf-8')).hexdigest()[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    return f"doc_{file_path.stem}_{timestamp}_{path_hash}"


def validate_file_format(file_path: Union[str, Path]) -> str:
    """
    验证文件格式
    
    Args:
        file_path: 文件路径
    
    Returns:
        文件格式（扩展名）
    
    Raises:
        FileFormatError: 不支持的文件格式
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        raise FileFormatError(f"文件不存在: {file_path}", str(file_path))
    
    # 支持的文件格式
    supported_formats = {'.pdf', '.docx', '.doc', '.txt', '.rtf'}
    
    file_extension = file_path.suffix.lower()
    if file_extension not in supported_formats:
        raise FileFormatError(
            f"不支持的文件格式: {file_extension}",
            str(file_path),
            ", ".join(supported_formats)
        )
    
    return file_extension


def clean_text(text: str, remove_extra_spaces: bool = True, remove_line_breaks: bool = True) -> str:
    """
    清理文本内容
    
    Args:
        text: 原始文本
        remove_extra_spaces: 是否删除多余空格
        remove_line_breaks: 是否删除换行符
    
    Returns:
        清理后的文本
    """
    if not text:
        return ""
    
    # 移除控制字符
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
    
    # 移除多余的空白字符
    if remove_extra_spaces:
        text = re.sub(r'\s+', ' ', text)
    
    # 移除换行符
    if remove_line_breaks:
        text = text.replace('\n', ' ').replace('\r', ' ')
    
    return text.strip()


def detect_segment_type(content: str, font_info: Optional[Dict[str, Any]] = None) -> str:
    """
    检测段落类型
    
    Args:
        content: 段落内容
        font_info: 字体信息
    
    Returns:
        段落类型
    """
    content = content.strip()
    
    if not content:
        return "empty"
    
    # 检测标题（基于字体大小或样式）
    if font_info:
        font_size = font_info.get('size', 12)
        is_bold = font_info.get('weight', 'normal') == 'bold'
        if font_size > 14 or is_bold:
            return "header"
    
    # 检测列表项
    if re.match(r'^[\s]*[•\-\*\d]+[\.\)]\s+', content):
        return "list_item"
    
    # 检测表格数据（包含制表符或多个数字）
    if '\t' in content or re.search(r'\d+\s+\d+\s+\d+', content):
        return "table"
    
    # 检测图片说明
    if re.match(r'^(图|表|Figure|Table)\s*\d+', content, re.IGNORECASE):
        return "caption"
    
    # 默认为段落
    return "paragraph"


def merge_segments(segments: List[Dict[str, Any]], max_distance: float = 50.0) -> List[Dict[str, Any]]:
    """
    合并相邻的段落
    
    Args:
        segments: 段落列表
        max_distance: 最大合并距离
    
    Returns:
        合并后的段落列表
    """
    if not segments:
        return []
    
    # 按页码和位置排序
    sorted_segments = sorted(segments, key=lambda x: (x.get('page_number', 0), x.get('position', {}).get('y', 0)))
    
    merged = []
    current_segment = None
    
    for segment in sorted_segments:
        if current_segment is None:
            current_segment = segment.copy()
            continue
        
        # 检查是否可以合并
        if _can_merge_segments(current_segment, segment, max_distance):
            # 合并内容
            current_segment['content'] += ' ' + segment['content']
            
            # 更新位置信息
            if 'position' in current_segment and 'position' in segment:
                current_pos = current_segment['position']
                segment_pos = segment['position']
                
                # 扩展边界框
                current_pos['width'] = max(
                    current_pos.get('x', 0) + current_pos.get('width', 0),
                    segment_pos.get('x', 0) + segment_pos.get('width', 0)
                ) - current_pos.get('x', 0)
                
                current_pos['height'] = max(
                    current_pos.get('y', 0) + current_pos.get('height', 0),
                    segment_pos.get('y', 0) + segment_pos.get('height', 0)
                ) - current_pos.get('y', 0)
        else:
            # 保存当前段落并开始新的段落
            merged.append(current_segment)
            current_segment = segment.copy()
    
    # 添加最后一个段落
    if current_segment:
        merged.append(current_segment)
    
    return merged


def _can_merge_segments(seg1: Dict[str, Any], seg2: Dict[str, Any], max_distance: float) -> bool:
    """
    检查两个段落是否可以合并
    
    Args:
        seg1: 第一个段落
        seg2: 第二个段落
        max_distance: 最大距离
    
    Returns:
        是否可以合并
    """
    # 必须在同一页
    if seg1.get('page_number') != seg2.get('page_number'):
        return False
    
    # 必须是相同类型
    if seg1.get('segment_type') != seg2.get('segment_type'):
        return False
    
    # 检查距离
    pos1 = seg1.get('position', {})
    pos2 = seg2.get('position', {})
    
    if not pos1 or not pos2:
        return False
    
    # 计算垂直距离
    y1 = pos1.get('y', 0) + pos1.get('height', 0)
    y2 = pos2.get('y', 0)
    
    distance = abs(y2 - y1)
    
    return distance <= max_distance


def calculate_text_similarity(text1: str, text2: str) -> float:
    """
    计算两个文本的相似度
    
    Args:
        text1: 第一个文本
        text2: 第二个文本
    
    Returns:
        相似度分数 (0-1)
    """
    # 简单的基于词汇重叠的相似度计算
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    
    return len(intersection) / len(union) if union else 0.0


def split_text_into_chunks(text: str, max_length: int = 512, overlap: int = 50) -> List[str]:
    """
    将长文本分割为块
    
    Args:
        text: 原始文本
        max_length: 最大块长度
        overlap: 重叠字符数
    
    Returns:
        文本块列表
    """
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + max_length
        
        # 尝试在句子边界处分割
        if end < len(text):
            # 寻找最近的句号、问号或感叹号
            sentence_end = text.rfind('.', start, end)
            if sentence_end == -1:
                sentence_end = text.rfind('?', start, end)
            if sentence_end == -1:
                sentence_end = text.rfind('!', start, end)
            
            if sentence_end != -1 and sentence_end > start + max_length // 2:
                end = sentence_end + 1
        
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        
        # 计算下一个开始位置（考虑重叠）
        start = max(start + 1, end - overlap)
        
        # 防止无限循环
        if start >= len(text):
            break
    
    return chunks


def validate_embeddings(embeddings: List[List[float]], expected_dimension: int) -> bool:
    """
    验证嵌入向量
    
    Args:
        embeddings: 嵌入向量列表
        expected_dimension: 期望的维度
    
    Returns:
        是否有效
    
    Raises:
        ValidationError: 验证失败
    """
    if not embeddings:
        raise ValidationError("嵌入向量列表不能为空")
    
    for i, embedding in enumerate(embeddings):
        if not isinstance(embedding, list):
            raise ValidationError(f"嵌入向量 {i} 不是列表类型", "embedding", type(embedding))
        
        if len(embedding) != expected_dimension:
            raise ValidationError(
                f"嵌入向量 {i} 维度不匹配",
                "dimension",
                f"期望 {expected_dimension}，实际 {len(embedding)}"
            )
        
        if not all(isinstance(x, (int, float)) for x in embedding):
            raise ValidationError(f"嵌入向量 {i} 包含非数值类型", "embedding", embedding)
    
    return True


def calculate_embedding_statistics(embeddings: np.ndarray) -> Dict[str, Any]:
    """
    计算嵌入向量的统计信息
    
    Args:
        embeddings: 嵌入向量矩阵
    
    Returns:
        统计信息字典
    """
    if embeddings.size == 0:
        return {}
    
    return {
        'shape': embeddings.shape,
        'mean': float(np.mean(embeddings)),
        'std': float(np.std(embeddings)),
        'min': float(np.min(embeddings)),
        'max': float(np.max(embeddings)),
        'norm_mean': float(np.mean(np.linalg.norm(embeddings, axis=1))),
        'norm_std': float(np.std(np.linalg.norm(embeddings, axis=1)))
    }


def create_processing_report(
    document_id: str,
    processing_time: float,
    total_segments: int,
    successful_embeddings: int,
    errors: List[str]
) -> Dict[str, Any]:
    """
    创建处理报告
    
    Args:
        document_id: 文档ID
        processing_time: 处理时间（秒）
        total_segments: 总段落数
        successful_embeddings: 成功嵌入数
        errors: 错误列表
    
    Returns:
        处理报告字典
    """
    return {
        'document_id': document_id,
        'timestamp': datetime.now().isoformat(),
        'processing_time_seconds': processing_time,
        'total_segments': total_segments,
        'successful_embeddings': successful_embeddings,
        'success_rate': successful_embeddings / total_segments if total_segments > 0 else 0.0,
        'errors': errors,
        'error_count': len(errors),
        'status': 'success' if len(errors) == 0 else 'partial_success' if successful_embeddings > 0 else 'failure'
    } 