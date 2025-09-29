"""
简化的PDF内容提取器

逐页提取PDF文本，自动生成段落标签，输出markdown格式。
"""

import hashlib
from typing import List, Tuple
from pathlib import Path

import fitz  # PyMuPDF
from loguru import logger

from .models import TextSegment, DocumentContent, ProcessingConfig
from .exceptions import ContentExtractionError


class ContentExtractor:
    """
    简化的PDF内容提取器
    
    功能：
    1. 逐页提取PDF文本
    2. 自动生成段落标签
    3. 输出markdown格式
    """
    
    def __init__(self, config: ProcessingConfig = None):
        """初始化提取器"""
        self.config = config or ProcessingConfig()
        self.logger = logger.bind(component="ContentExtractor")
    
    def extract_pdf(self, file_path: str) -> DocumentContent:
        """
        提取PDF内容
        
        Args:
            file_path: PDF文件路径
            
        Returns:
            文档内容对象
        """
        file_path = Path(file_path)
        self.logger.info(f"开始提取PDF: {file_path}")
        
        try:
            # 打开PDF文档
            doc = fitz.open(str(file_path))
            
            # 提取所有段落
            all_segments = []
            markdown_lines = []
            
            # 逐页提取
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_segments = self._extract_page_text(page, page_num + 1)
                
                all_segments.extend(page_segments)
                
                # 添加到markdown
                if page_segments:
                    markdown_lines.append(f"\n## 第 {page_num + 1} 页\n")
                    for segment in page_segments:
                        markdown_lines.append(f"**{segment.segment_id}**\n\n{segment.content}\n\n---\n")
            
            doc.close()
            
            # 生成文档ID
            document_id = self._generate_document_id(file_path)
            
            # 合并markdown内容
            markdown_content = "".join(markdown_lines)
            
            # 创建文档内容对象
            document_content = DocumentContent(
                document_id=document_id,
                file_path=str(file_path),
                segments=all_segments,
                markdown_content=markdown_content
            )
            
            self.logger.info(f"提取完成: {len(all_segments)} 个段落")
            return document_content
            
        except Exception as e:
            raise ContentExtractionError(f"PDF提取失败: {e}", str(file_path))
    
    def _extract_page_text(self, page: fitz.Page, page_number: int) -> List[TextSegment]:
        """
        提取单页文本
        
        Args:
            page: PDF页面对象
            page_number: 页码
            
        Returns:
            文本段落列表
        """
        segments = []
        
        try:
            # 提取表格（优先处理）
            table_segments = self._extract_page_tables(page, page_number)
            segments.extend(table_segments)
            
            # 获取文本块
            text_dict = page.get_text("dict")
            
            # 收集所有文本块及其位置信息
            text_blocks = []
            for block_idx, block in enumerate(text_dict["blocks"]):
                if "lines" not in block:
                    continue
                
                # 合并行文本
                block_text = ""
                for line in block["lines"]:
                    for span in line["spans"]:
                        block_text += span["text"]
                
                # 清理文本
                clean_text = self._clean_text(block_text)
                
                # 过滤短文本
                if len(clean_text) >= self.config.min_text_length:
                    # 获取位置信息
                    bbox = block["bbox"]
                    position_y = bbox[1]  # Y坐标
                    position_x = bbox[0]  # X坐标
                    
                    text_blocks.append({
                        'text': clean_text,
                        'position_y': position_y,
                        'position_x': position_x,
                        'bbox': bbox,
                        'original_index': block_idx
                    })
            
            # 按位置排序：先按Y坐标（从上到下），再按X坐标（从左到右）
            text_blocks.sort(key=lambda x: (x['position_y'], x['position_x']))
            
            # 生成段落
            for segment_idx, block in enumerate(text_blocks):
                # 生成段落ID（使用排序后的索引）
                segment_id = f"P{page_number:03d}_S{segment_idx:03d}"
                
                # 创建文本段落
                segment = TextSegment(
                    segment_id=segment_id,
                    content=block['text'],
                    page_number=page_number,
                    position_y=block['position_y']
                )
                
                segments.append(segment)
        
        except Exception as e:
            self.logger.warning(f"页面 {page_number} 提取失败: {e}")
        
        return segments
    
    def _extract_page_tables(self, page: fitz.Page, page_number: int) -> List[TextSegment]:
        """
        提取页面表格
        
        Args:
            page: PDF页面对象
            page_number: 页码
            
        Returns:
            表格段落列表
        """
        segments = []
        
        try:
            # 查找表格
            tables = page.find_tables()
            
            for table_idx, table in enumerate(tables):
                # 提取表格数据
                table_data = table.extract()
                
                if table_data and len(table_data) > 0:
                    # 转换为Markdown表格格式
                    table_markdown = self._convert_table_to_markdown(table_data)
                    
                    if len(table_markdown) >= self.config.min_text_length:
                        # 获取表格位置
                        table_bbox = table.bbox
                        position_y = table_bbox[1]
                        
                        # 生成表格段落ID
                        segment_id = f"P{page_number:03d}_T{table_idx:03d}"
                        
                        # 创建表格段落
                        segment = TextSegment(
                            segment_id=segment_id,
                            content=f"**[表格]**\n\n{table_markdown}",
                            page_number=page_number,
                            position_y=position_y
                        )
                        
                        segments.append(segment)
                        
        except Exception as e:
            self.logger.warning(f"页面 {page_number} 表格提取失败: {e}")
        
        return segments
    
    def _convert_table_to_markdown(self, table_data: list) -> str:
        """
        将表格数据转换为Markdown格式
        
        Args:
            table_data: 表格数据（二维列表）
            
        Returns:
            Markdown格式的表格
        """
        if not table_data or len(table_data) == 0:
            return ""
        
        # 清理表格数据
        cleaned_data = []
        for row in table_data:
            cleaned_row = [self._clean_text(cell) if cell else "" for cell in row]
            cleaned_data.append(cleaned_row)
        
        # 生成Markdown表格
        markdown_lines = []
        
        # 表头
        if cleaned_data:
            header = "| " + " | ".join(cleaned_data[0]) + " |"
            markdown_lines.append(header)
            
            # 分隔线
            separator = "| " + " | ".join(["-" * max(3, len(cell)) for cell in cleaned_data[0]]) + " |"
            markdown_lines.append(separator)
            
            # 表格内容
            for row in cleaned_data[1:]:
                content_row = "| " + " | ".join(row) + " |"
                markdown_lines.append(content_row)
        
        return "\n".join(markdown_lines)
    
    def _clean_text(self, text: str) -> str:
        """
        清理文本
        
        Args:
            text: 原始文本
            
        Returns:
            清理后的文本
        """
        if not text:
            return ""
        
        # 移除多余空格和换行
        text = " ".join(text.split())
        
        # 移除控制字符
        text = ''.join(char for char in text if ord(char) >= 32)
        
        return text.strip()
    
    def _generate_document_id(self, file_path: Path) -> str:
        """
        生成文档ID
        
        Args:
            file_path: 文件路径
            
        Returns:
            文档ID
        """
        # 使用文件名和路径生成哈希
        content = f"{file_path.name}_{file_path.stat().st_size}"
        hash_value = hashlib.md5(content.encode()).hexdigest()[:8]
        return f"doc_{file_path.stem}_{hash_value}"
    
    def save_markdown(self, document_content: DocumentContent, output_path: str = None) -> str:
        """
        保存markdown文件
        
        Args:
            document_content: 文档内容
            output_path: 输出路径，如果不指定则自动生成
            
        Returns:
            保存的文件路径
        """
        if output_path is None:
            # 自动生成输出路径
            original_path = Path(document_content.file_path)
            output_path = original_path.parent / f"{original_path.stem}_extracted.md"
        
        output_path = Path(output_path)
        
        try:
            # 生成完整的markdown内容
            markdown_content = f"""# {Path(document_content.file_path).name}

**文档ID**: {document_content.document_id}  
**提取时间**: {document_content.created_at.strftime('%Y-%m-%d %H:%M:%S')}  
**总段落数**: {len(document_content.segments)}

---

{document_content.markdown_content}

---

## 段落索引

| 段落ID | 页码 | 内容预览 |
|--------|------|----------|
"""
            
            # 添加段落索引
            for segment in document_content.segments:
                preview = segment.content[:50] + "..." if len(segment.content) > 50 else segment.content
                markdown_content += f"| {segment.segment_id} | {segment.page_number} | {preview} |\n"
            
            # 写入文件
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
            
            self.logger.info(f"Markdown文件已保存: {output_path}")
            return str(output_path)
            
        except Exception as e:
            raise ContentExtractionError(f"保存Markdown失败: {e}", str(output_path)) 