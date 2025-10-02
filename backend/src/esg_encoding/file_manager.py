"""
文件管理服务
负责处理文件上传、存储、移动和清理
"""

import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List
from loguru import logger
import hashlib
import json

class FileManager:
    """ESG系统文件管理器"""

    def __init__(self, base_upload_dir: str = "../../uploads"):
        # Use absolute path from file location to ensure correct uploads directory
        # Path: backend/src/esg_encoding/ -> backend/ -> ESG DEMO/ -> uploads/
        if base_upload_dir == "../../uploads":
            self.base_dir = Path(__file__).parent.parent.parent.parent / "uploads"
        else:
            self.base_dir = Path(base_upload_dir)
        self.reports_dir = self.base_dir / "reports"
        self.metrics_dir = self.base_dir / "metrics"
        self.outputs_dir = self.base_dir / "outputs"
        
        # 子目录
        self.pending_reports = self.reports_dir / "pending"
        self.processed_reports = self.reports_dir / "processed"
        self.failed_reports = self.reports_dir / "failed"
        
        self.excel_metrics = self.metrics_dir / "excel"
        self.json_metrics = self.metrics_dir / "json"
        
        self.compliance_outputs = self.outputs_dir / "compliance_reports"
        self.markdown_outputs = self.outputs_dir / "markdown"
        self.embeddings_outputs = self.outputs_dir / "embeddings"
        
        # 确保所有目录存在
        self._create_directories()
        
        # 文件元数据存储
        self.metadata_file = self.base_dir / "file_metadata.json"
        self.metadata = self._load_metadata()
    
    def _create_directories(self):
        """创建所有必要的目录"""
        directories = [
            self.pending_reports,
            self.processed_reports,
            self.failed_reports,
            self.excel_metrics,
            self.json_metrics,
            self.compliance_outputs,
            self.markdown_outputs,
            self.embeddings_outputs
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            logger.info(f"确保目录存在: {directory}")
    
    def _load_metadata(self) -> Dict:
        """加载文件元数据"""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载元数据失败: {e}")
        return {"files": {}, "sessions": {}}
    
    def _save_metadata(self):
        """保存文件元数据"""
        try:
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存元数据失败: {e}")
    
    def _generate_file_hash(self, file_path: Path) -> str:
        """生成文件哈希值"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def save_uploaded_file(self, file_content: bytes, filename: str, 
                          file_type: str = "report", industry: str = None,
                          framework: str = None, semi_industry: str = None) -> Dict[str, str]:
        """
        保存上传的文件
        
        Args:
            file_content: 文件内容
            filename: 原始文件名
            file_type: 文件类型 ('report', 'metrics')
            
        Returns:
            文件信息字典
        """
        # 生成唯一文件ID
        file_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 获取文件扩展名
        file_extension = Path(filename).suffix
        safe_filename = f"{timestamp}_{file_id}{file_extension}"
        
        # 根据文件类型选择存储目录
        if file_type == "report":
            target_dir = self.pending_reports
        elif file_type == "metrics":
            if file_extension.lower() in ['.xlsx', '.xls']:
                target_dir = self.excel_metrics
            else:
                target_dir = self.json_metrics
        else:
            raise ValueError(f"不支持的文件类型: {file_type}")
        
        target_path = target_dir / safe_filename
        
        # 保存文件
        try:
            with open(target_path, 'wb') as f:
                f.write(file_content)
            
            # 生成文件哈希
            file_hash = self._generate_file_hash(target_path)
            
            # 记录文件元数据
            file_info = {
                "file_id": file_id,
                "original_name": filename,
                "safe_filename": safe_filename,
                "file_path": str(target_path),
                "file_type": file_type,
                "file_size": len(file_content),
                "file_hash": file_hash,
                "upload_time": datetime.now().isoformat(),
                "status": "pending" if file_type == "report" else "uploaded",
                "processing_history": [],
                "industry": industry,
                "framework": framework,
                "semi_industry": semi_industry
            }
            
            self.metadata["files"][file_id] = file_info
            self._save_metadata()
            
            logger.info(f"文件保存成功: {filename} -> {safe_filename}")
            return file_info
            
        except Exception as e:
            logger.error(f"保存文件失败: {e}")
            # 清理可能创建的文件
            if target_path.exists():
                target_path.unlink()
            raise
    
    def move_report_file(self, file_id: str, status: str) -> bool:
        """
        移动报告文件到对应状态目录
        
        Args:
            file_id: 文件ID
            status: 目标状态 ('processed', 'failed')
            
        Returns:
            是否移动成功
        """
        if file_id not in self.metadata["files"]:
            logger.error(f"文件ID不存在: {file_id}")
            return False
        
        file_info = self.metadata["files"][file_id]
        current_path = Path(file_info["file_path"])
        
        if not current_path.exists():
            logger.error(f"源文件不存在: {current_path}")
            return False
        
        # 确定目标目录
        if status == "processed":
            target_dir = self.processed_reports
        elif status == "failed":
            target_dir = self.failed_reports
        else:
            logger.error(f"不支持的状态: {status}")
            return False
        
        target_path = target_dir / current_path.name
        
        try:
            shutil.move(str(current_path), str(target_path))
            
            # 更新元数据
            file_info["file_path"] = str(target_path)
            file_info["status"] = status
            file_info["processing_history"].append({
                "timestamp": datetime.now().isoformat(),
                "action": f"moved_to_{status}",
                "previous_path": str(current_path),
                "new_path": str(target_path)
            })
            
            self._save_metadata()
            logger.info(f"文件移动成功: {current_path} -> {target_path}")
            return True
            
        except Exception as e:
            logger.error(f"移动文件失败: {e}")
            return False
    
    def get_file_info(self, file_id: str) -> Optional[Dict]:
        """获取文件信息"""
        return self.metadata["files"].get(file_id)
    
    def list_files_by_type(self, file_type: str, status: Optional[str] = None) -> List[Dict]:
        """
        按类型和状态列出文件
        
        Args:
            file_type: 文件类型
            status: 文件状态（可选）
            
        Returns:
            文件信息列表
        """
        files = []
        for file_id, file_info in self.metadata["files"].items():
            if file_info["file_type"] == file_type:
                if status is None or file_info["status"] == status:
                    files.append(file_info)
        
        # 按上传时间排序
        files.sort(key=lambda x: x["upload_time"], reverse=True)
        return files
    
    def cleanup_old_files(self, days: int = 30) -> int:
        """
        清理指定天数前的文件
        
        Args:
            days: 保留天数
            
        Returns:
            清理的文件数量
        """
        cutoff_time = datetime.now() - timedelta(days=days)
        cleaned_count = 0
        
        files_to_remove = []
        for file_id, file_info in self.metadata["files"].items():
            upload_time = datetime.fromisoformat(file_info["upload_time"])
            if upload_time < cutoff_time:
                file_path = Path(file_info["file_path"])
                if file_path.exists():
                    try:
                        file_path.unlink()
                        logger.info(f"清理旧文件: {file_path}")
                        cleaned_count += 1
                    except Exception as e:
                        logger.error(f"清理文件失败: {e}")
                
                files_to_remove.append(file_id)
        
        # 从元数据中移除
        for file_id in files_to_remove:
            del self.metadata["files"][file_id]
        
        if files_to_remove:
            self._save_metadata()
        
        logger.info(f"清理完成，共清理 {cleaned_count} 个文件")
        return cleaned_count
    
    def get_storage_stats(self) -> Dict:
        """获取存储统计信息"""
        stats = {
            "total_files": len(self.metadata["files"]),
            "by_type": {},
            "by_status": {},
            "storage_size": 0,
            "directories": {}
        }
        
        # 按类型和状态统计
        for file_info in self.metadata["files"].values():
            file_type = file_info["file_type"]
            status = file_info["status"]
            file_size = file_info.get("file_size", 0)
            
            stats["by_type"][file_type] = stats["by_type"].get(file_type, 0) + 1
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
            stats["storage_size"] += file_size
        
        # 目录大小统计
        def get_dir_size(directory):
            total_size = 0
            if directory.exists():
                for path in directory.rglob('*'):
                    if path.is_file():
                        total_size += path.stat().st_size
            return total_size
        
        stats["directories"] = {
            "reports": get_dir_size(self.reports_dir),
            "metrics": get_dir_size(self.metrics_dir),
            "outputs": get_dir_size(self.outputs_dir)
        }
        
        return stats


# 全局文件管理器实例
file_manager = FileManager()