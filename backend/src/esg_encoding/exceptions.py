"""
ESG报告编码的异常类定义

定义了系统中使用的所有自定义异常类。
"""

from typing import Optional, Any


class ESGEncodingError(Exception):
    """ESG编码模块的基础异常类"""
    
    def __init__(self, message: str, error_code: Optional[str] = None, details: Optional[Any] = None) -> None:
        """
        初始化异常
        
        Args:
            message: 错误消息
            error_code: 错误代码
            details: 错误详情
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details
    
    def __str__(self) -> str:
        """返回异常的字符串表示"""
        if self.error_code:
            return f"[{self.error_code}] {self.message}"
        return self.message


class ContentExtractionError(ESGEncodingError):
    """内容提取异常"""
    
    def __init__(self, message: str, file_path: Optional[str] = None, page_number: Optional[int] = None) -> None:
        """
        初始化内容提取异常
        
        Args:
            message: 错误消息
            file_path: 文件路径
            page_number: 页码
        """
        super().__init__(message, "CONTENT_EXTRACTION_ERROR")
        self.file_path = file_path
        self.page_number = page_number
    
    def __str__(self) -> str:
        """返回异常的字符串表示"""
        base_msg = super().__str__()
        if self.file_path:
            base_msg += f" (文件: {self.file_path}"
            if self.page_number:
                base_msg += f", 页码: {self.page_number}"
            base_msg += ")"
        return base_msg


class SegmentStructuringError(ESGEncodingError):
    """段落结构化异常"""
    
    def __init__(self, message: str, segment_id: Optional[str] = None, segment_type: Optional[str] = None) -> None:
        """
        初始化段落结构化异常
        
        Args:
            message: 错误消息
            segment_id: 段落ID
            segment_type: 段落类型
        """
        super().__init__(message, "SEGMENT_STRUCTURING_ERROR")
        self.segment_id = segment_id
        self.segment_type = segment_type
    
    def __str__(self) -> str:
        """返回异常的字符串表示"""
        base_msg = super().__str__()
        if self.segment_id:
            base_msg += f" (段落ID: {self.segment_id}"
            if self.segment_type:
                base_msg += f", 类型: {self.segment_type}"
            base_msg += ")"
        return base_msg


class ContentEmbeddingError(ESGEncodingError):
    """内容嵌入异常"""
    
    def __init__(self, message: str, model_name: Optional[str] = None, batch_size: Optional[int] = None) -> None:
        """
        初始化内容嵌入异常
        
        Args:
            message: 错误消息
            model_name: 模型名称
            batch_size: 批处理大小
        """
        super().__init__(message, "CONTENT_EMBEDDING_ERROR")
        self.model_name = model_name
        self.batch_size = batch_size
    
    def __str__(self) -> str:
        """返回异常的字符串表示"""
        base_msg = super().__str__()
        if self.model_name:
            base_msg += f" (模型: {self.model_name}"
            if self.batch_size:
                base_msg += f", 批大小: {self.batch_size}"
            base_msg += ")"
        return base_msg


class ModelLoadError(ESGEncodingError):
    """模型加载异常"""
    
    def __init__(self, message: str, model_name: str, model_path: Optional[str] = None) -> None:
        """
        初始化模型加载异常
        
        Args:
            message: 错误消息
            model_name: 模型名称
            model_path: 模型路径
        """
        super().__init__(message, "MODEL_LOAD_ERROR")
        self.model_name = model_name
        self.model_path = model_path
    
    def __str__(self) -> str:
        """返回异常的字符串表示"""
        base_msg = super().__str__()
        base_msg += f" (模型: {self.model_name}"
        if self.model_path:
            base_msg += f", 路径: {self.model_path}"
        base_msg += ")"
        return base_msg


class ConfigurationError(ESGEncodingError):
    """配置异常"""
    
    def __init__(self, message: str, config_key: Optional[str] = None, config_value: Optional[Any] = None) -> None:
        """
        初始化配置异常
        
        Args:
            message: 错误消息
            config_key: 配置键
            config_value: 配置值
        """
        super().__init__(message, "CONFIGURATION_ERROR")
        self.config_key = config_key
        self.config_value = config_value
    
    def __str__(self) -> str:
        """返回异常的字符串表示"""
        base_msg = super().__str__()
        if self.config_key:
            base_msg += f" (配置: {self.config_key}"
            if self.config_value is not None:
                base_msg += f"={self.config_value}"
            base_msg += ")"
        return base_msg


class FileFormatError(ESGEncodingError):
    """文件格式异常"""
    
    def __init__(self, message: str, file_path: str, expected_format: Optional[str] = None) -> None:
        """
        初始化文件格式异常
        
        Args:
            message: 错误消息
            file_path: 文件路径
            expected_format: 期望的文件格式
        """
        super().__init__(message, "FILE_FORMAT_ERROR")
        self.file_path = file_path
        self.expected_format = expected_format
    
    def __str__(self) -> str:
        """返回异常的字符串表示"""
        base_msg = super().__str__()
        base_msg += f" (文件: {self.file_path}"
        if self.expected_format:
            base_msg += f", 期望格式: {self.expected_format}"
        base_msg += ")"
        return base_msg


class ProcessingTimeoutError(ESGEncodingError):
    """处理超时异常"""
    
    def __init__(self, message: str, timeout_seconds: Optional[float] = None, operation: Optional[str] = None) -> None:
        """
        初始化处理超时异常
        
        Args:
            message: 错误消息
            timeout_seconds: 超时时间（秒）
            operation: 操作名称
        """
        super().__init__(message, "PROCESSING_TIMEOUT_ERROR")
        self.timeout_seconds = timeout_seconds
        self.operation = operation
    
    def __str__(self) -> str:
        """返回异常的字符串表示"""
        base_msg = super().__str__()
        if self.operation:
            base_msg += f" (操作: {self.operation}"
            if self.timeout_seconds:
                base_msg += f", 超时: {self.timeout_seconds}s"
            base_msg += ")"
        return base_msg


class ValidationError(ESGEncodingError):
    """数据验证异常"""
    
    def __init__(self, message: str, field_name: Optional[str] = None, invalid_value: Optional[Any] = None) -> None:
        """
        初始化数据验证异常
        
        Args:
            message: 错误消息
            field_name: 字段名称
            invalid_value: 无效值
        """
        super().__init__(message, "VALIDATION_ERROR")
        self.field_name = field_name
        self.invalid_value = invalid_value
    
    def __str__(self) -> str:
        """返回异常的字符串表示"""
        base_msg = super().__str__()
        if self.field_name:
            base_msg += f" (字段: {self.field_name}"
            if self.invalid_value is not None:
                base_msg += f", 值: {self.invalid_value}"
            base_msg += ")"
        return base_msg 