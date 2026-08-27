"""
文件管理服务
负责处理文件上传、存储、移动和清理
"""

import os
import re
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any
from loguru import logger
import hashlib
import json
import threading

import numpy as np

from .models import ReportContent, TextSegment
from .retrieval.metric_corpus import (
    METRIC_CORPUS_SCHEMA_VERSION,
    MetricRetrievalCorpus,
)


def _canonical_segments_digest(segments: List[TextSegment]) -> str:
    """Stable disk identity for the canonical evidence used by metric sidecars."""
    digest = hashlib.sha256()
    for segment in segments:
        structured = getattr(segment, "structured_data", None)
        structured = structured if isinstance(structured, dict) else {}
        payload = {
            "segment_id": str(getattr(segment, "segment_id", "") or ""),
            "content": str(getattr(segment, "content", "") or ""),
            "page_number": int(getattr(segment, "page_number", 0) or 0),
            "position_y": getattr(segment, "position_y", None),
            "position_x": getattr(segment, "position_x", None),
            "segment_type": str(getattr(segment, "segment_type", "") or ""),
            "source_table_id": str(
                getattr(segment, "source_table_id", None)
                or structured.get("source_table_id")
                or structured.get("table_id")
                or ""
            ),
            "row_header": getattr(segment, "row_header", None),
            "col_header": getattr(segment, "col_header", None),
            "value_text": getattr(segment, "value_text", None),
            "unit": getattr(segment, "unit", None),
            "header_path": list(getattr(segment, "header_path", None) or []),
            "rowspan": getattr(segment, "rowspan", None),
            "colspan": getattr(segment, "colspan", None),
            "parse_pass": getattr(segment, "parse_pass", None),
            "review_status": getattr(segment, "review_status", None),
            "conflicts": list(getattr(segment, "conflicts", None) or []),
            "row_index": structured.get("row_index", structured.get("row_idx")),
            "col_index": structured.get("col_index", structured.get("column_index")),
            "structured_data": structured,
        }
        digest.update(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _ordered_ids_digest(values: List[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _safe_pdf_page_count_from_bytes(pdf_bytes: bytes) -> Optional[int]:
    """Best-effort PDF page count from bytes.

    Returns None if the payload is not a valid PDF or PyMuPDF is unavailable.
    """
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return int(doc.page_count)
    except Exception:
        return None


def _safe_pdf_page_count_from_path(pdf_path: Path) -> Optional[int]:
    """Best-effort PDF page count from a file path."""
    try:
        if not pdf_path.exists():
            return None
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf_path))
        return int(doc.page_count)
    except Exception:
        return None



def _normalize_report_key(value: str) -> str:
    """Normalize a report identifier (uuid/filename/stem) into a comparable key."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    if not s:
        return ""
    # Remove URL encoding artifacts
    try:
        from urllib.parse import unquote
        s = unquote(s)
    except Exception:
        pass
    # Remove common extensions
    s = Path(s).stem
    # Collapse to alnum only for fuzzy matching (Google2025 == Google 2025 == google_2025)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s
class FileManager:
    """ESG系统文件管理器"""

    def __init__(self, base_upload_dir: str = "../../uploads"):
        self._metadata_lock = threading.RLock()
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
            logger.debug(f"Ensured storage directory: {directory}")
    
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
        """Atomically persist metadata; never report success after a failed write."""
        temp_path: Optional[Path] = None
        try:
            with self._metadata_lock:
                self.metadata_file.parent.mkdir(parents=True, exist_ok=True)
                temp_path = self.metadata_file.with_name(
                    f".{self.metadata_file.name}.{uuid.uuid4().hex}.tmp"
                )
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self.metadata, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self.metadata_file)
        except Exception as e:
            logger.error(f"保存元数据失败: {e}")
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            raise

    def remove_file_metadata(self, file_id: str) -> bool:
        """Remove one metadata record and persist it as one locked operation."""
        with self._metadata_lock:
            files = self.metadata.setdefault("files", {})
            removed = files.pop(file_id, None)
            if removed is None:
                return False
            try:
                self._save_metadata()
            except Exception:
                # Keep memory and disk consistent when persistence fails.
                files[file_id] = removed
                raise
            return True
    
    def recover_interrupted_reports(self) -> Dict[str, int]:
        """Normalize in-memory report jobs lost when the backend stopped."""
        recovered = {"completed": 0, "interrupted": 0}
        changed = False
        now = datetime.now().isoformat()
        for info in self.metadata.get("files", {}).values():
            if not isinstance(info, dict) or info.get("file_type") != "report":
                continue
            status = str(info.get("status") or "").strip().lower()
            stage = str(info.get("processing_stage") or "").strip().lower()
            history = info.get("processing_history")
            last_history = history[-1] if isinstance(history, list) and history else {}
            legacy_partial_recovery = (
                status == "failed"
                and stage == "interrupted"
                and isinstance(last_history, dict)
                and last_history.get("stage") == "interrupted_recovery"
                and last_history.get("previous_stage") == "partial_success"
            )
            if status != "processing" and not legacy_partial_recovery:
                continue
            if legacy_partial_recovery:
                stage = "partial_success"
            previous_job_id = info.pop("processing_job_id", None)
            if stage in {"completed", "partial_success"}:
                info.pop("interrupted_job_id", None)
                recovered_error = info.get("processing_error")
                if stage == "partial_success" and (
                    not recovered_error
                    or legacy_partial_recovery
                ):
                    recovered_error = "Report content was processed, but assessment completed with warnings."
                info.update({
                    "status": "processed",
                    "processing_stage": stage,
                    "processing_progress": 100,
                    "processing_error": recovered_error if stage == "partial_success" else None,
                })
                recovered["completed"] += 1
            else:
                if previous_job_id:
                    info["interrupted_job_id"] = previous_job_id
                info.update({
                    "status": "failed",
                    "processing_stage": "interrupted",
                    "processing_progress": 100,
                    "processing_error": "Processing was interrupted by a backend restart. Please reprocess the report.",
                })
                recovered["interrupted"] += 1
            history = info.setdefault("processing_history", [])
            if isinstance(history, list):
                recovery_stage = "terminal_recovery" if stage in {"completed", "partial_success"} else "interrupted_recovery"
                history.append({"time": now, "stage": recovery_stage, "previous_stage": stage})
            changed = True
        if changed:
            self._save_metadata()
            logger.warning(
                "Recovered report metadata completed={} interrupted={}",
                recovered["completed"], recovered["interrupted"],
            )
        return recovered

    def _generate_file_hash(self, file_path: Path) -> str:
        """生成文件哈希值"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def save_uploaded_file(self, file_content: bytes, filename: str,
                          file_type: str = "report", industry: str = None,
                          framework: str = None, semi_industry: str = None,
                          gri_sector: Optional[str] = None,
                          gri_topic: Optional[str] = None,
                          user_id: Optional[int] = None) -> Dict[str, str]:
        """
        保存上传的文件

        Args:
            file_content: 文件内容
            filename: 原始文件名
            file_type: 文件类型 ('report', 'metrics')
            industry: 行业分类 (SASB)
            framework: 框架类型
            semi_industry: 子行业 (SASB)
            gri_sector: GRI 行业板块 slug (framework=GRI 时)
            gri_topic: GRI 主题 slug (framework=GRI 时)
            user_id: 用户ID

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
        
        # 保存文件。运行期间如果 uploads 子目录被清理，必须在写入前重新创建。
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(target_dir, 0o777)
            except Exception:
                pass
            with open(target_path, 'wb') as f:
                f.write(file_content)

            # Best-effort: compute PDF page count early so the dashboard can
            # display "No. of Pages" without relying on downstream processing.
            page_count: Optional[int] = None
            if file_type == "report" and file_extension.lower() == ".pdf":
                page_count = _safe_pdf_page_count_from_bytes(file_content)
            
            # 生成文件哈希
            file_hash = self._generate_file_hash(target_path)
            
            # 记录文件元数据（GRI 时保存 gri_sector/gri_topic 供列表展示）
            file_info = {
                "file_id": file_id,
                "original_name": filename,
                "safe_filename": safe_filename,
                "file_path": str(target_path),
                "file_type": file_type,
                "file_size": len(file_content),
                "page_count": page_count,
                "file_hash": file_hash,
                "upload_time": datetime.now().isoformat(),
                "status": "pending" if file_type == "report" else "uploaded",
                "processing_history": [],
                "industry": industry,
                "framework": framework,
                "semi_industry": semi_industry,
                "gri_sector": gri_sector,
                "gri_topic": gri_topic,
                "user_id": user_id
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
            # Move extracted markdown (created during PDF extraction) together with the PDF
            extracted_src = current_path.parent / f"{current_path.stem}_extracted.md"
            extracted_dst = target_dir / extracted_src.name
            visual_src = current_path.parent / f"{current_path.stem}_visual_assets"
            visual_dst = target_dir / visual_src.name

            already_in_target = current_path.resolve() == target_path.resolve()
            if not already_in_target:
                shutil.move(str(current_path), str(target_path))

            if extracted_src.exists() and extracted_src.resolve() != extracted_dst.resolve():
                try:
                    shutil.move(str(extracted_src), str(extracted_dst))
                    file_info["extracted_md_path"] = str(extracted_dst)
                    logger.info(f"Moved extracted markdown to: {extracted_dst}")
                except Exception as e:
                    logger.warning(f"Failed to move extracted markdown {extracted_src} -> {extracted_dst}: {e}")

            if visual_src.exists() and visual_src.resolve() != visual_dst.resolve():
                try:
                    shutil.copytree(visual_src, visual_dst, dirs_exist_ok=True)
                    shutil.rmtree(visual_src)
                    file_info["visual_assets_path"] = str(visual_dst)
                    logger.info(f"Moved visual assets to: {visual_dst}")
                except Exception as e:
                    logger.warning(f"Failed to move visual assets {visual_src} -> {visual_dst}: {e}")
            
            # Also support legacy markdown saved as <stem>.md (older runs)
            legacy_src = current_path.parent / f"{current_path.stem}.md"
            legacy_dst = target_dir / legacy_src.name
            if legacy_src.exists() and not extracted_src.exists():
                try:
                    shutil.move(str(legacy_src), str(legacy_dst))
                    file_info["extracted_md_path"] = str(legacy_dst)
                    logger.info(f"Moved legacy markdown to: {legacy_dst}")
                except Exception as e:
                    logger.warning(f"Failed to move legacy markdown {legacy_src} -> {legacy_dst}: {e}")


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

    def resolve_file_id(self, alias: str, user_id: Optional[int] = None) -> Optional[str]:
        """Resolve non-UUID aliases (e.g., 'Google2025') to the real internal file_id.

        This is needed because some cached cross-analysis JSON uses report short names as `id`,
        while backend endpoints expect the UUID-like file_id.

        Matching strategy:
        1) Exact key match in metadata
        2) Normalize and compare against original_name/safe_filename/file_id
        """
        if not alias:
            return None

        # Exact hit
        if alias in self.metadata.get("files", {}):
            return alias

        needle = _normalize_report_key(alias)
        if not needle:
            return None

        best_id: Optional[str] = None
        best_ts: str = ""

        for fid, info in (self.metadata.get("files", {}) or {}).items():
            if not isinstance(info, dict):
                continue

            # optional ownership constraint
            if user_id is not None:
                file_user_id = info.get("user_id")
                if file_user_id is not None and file_user_id != user_id:
                    continue

            # Only consider report PDFs for alias resolution
            if info.get("file_type") and str(info.get("file_type")) != "report":
                continue

            cands = [
                fid,
                info.get("file_id"),
                info.get("safe_filename"),
                info.get("original_name"),
            ]
            hit = False
            for c in cands:
                if not c:
                    continue
                if _normalize_report_key(str(c)) == needle:
                    hit = True
                    break

            if not hit:
                continue

            # Prefer latest upload_time if multiple match
            ts = str(info.get("upload_time") or "")
            if ts >= best_ts:
                best_ts = ts
                best_id = fid

        return best_id

    
    def get_file_info(self, file_id: str, user_id: Optional[int] = None) -> Optional[Dict]:
        """
        获取文件信息

        Args:
            file_id: 文件ID（优先 uuid；也允许传入文件名/公司简称用于兼容旧前端）
            user_id: 用户ID (如果提供,会检查文件是否属于该用户)

        Returns:
            文件信息字典,如果文件不存在或不属于该用户则返回None
        """
        if not file_id:
            return None

        # 1) Exact match (uuid)
        file_info = self.metadata["files"].get(file_id)

        # 2) Backward compatible: allow passing filename stem / display name.
        #    Example: Google2025 -> resolve to its uuid.
        if not file_info:
            wanted = _normalize_report_key(file_id)
            if wanted:
                best = None
                best_time = ""
                for fid, info in (self.metadata.get("files") or {}).items():
                    if not isinstance(info, dict):
                        continue
                    # Only reports have PDFs
                    if info.get("file_type") != "report":
                        continue
                    if user_id is not None:
                        file_user_id = info.get("user_id")
                        if file_user_id is not None and file_user_id != user_id:
                            continue

                    candidates = [
                        info.get("file_id"),
                        info.get("original_name"),
                        info.get("safe_filename"),
                        info.get("file_path"),
                    ]
                    if any(_normalize_report_key(c) == wanted for c in candidates if c):
                        # Prefer latest upload_time if multiple matches
                        ut = str(info.get("upload_time") or "")
                        if not best or ut > best_time:
                            best = info
                            best_time = ut
                if best:
                    file_info = best

        if not file_info:
            return None

        # If provided, check ownership
        if user_id is not None:
            file_user_id = file_info.get("user_id")
            if file_user_id is not None and file_user_id != user_id:
                return None

        return file_info

    # =============================
    # Embeddings / Segments artifacts
    # =============================

    def _metric_manifest_path(self, file_id: str) -> Path:
        safe_file_id = str(file_id or "").strip()
        if (
            not safe_file_id
            or Path(safe_file_id).name != safe_file_id
            or safe_file_id in {".", ".."}
        ):
            raise ValueError("Invalid report file ID for metric artifacts")
        return self.embeddings_outputs / f"{safe_file_id}_metric_retrieval_manifest.json"

    @staticmethod
    def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def save_metric_retrieval_artifacts(
        self,
        file_id: str,
        corpus: MetricRetrievalCorpus,
        source_segments: List[TextSegment],
    ) -> Dict[str, str]:
        """Atomically publish an optional structure-preserving metric corpus."""
        self.embeddings_outputs.mkdir(parents=True, exist_ok=True)
        manifest_path = self._metric_manifest_path(file_id)
        views = list(corpus.retrieval_views or [])
        view_ids = [view.view_id for view in views]
        matrix = np.asarray(
            getattr(corpus, "_embedding_matrix", None),
            dtype=np.float32,
        )
        embedded_ids = [
            str(value)
            for value in (getattr(corpus, "_embedding_view_ids", None) or [])
        ]
        if (
            matrix.ndim != 2
            or matrix.shape[0] != len(views)
            or embedded_ids != view_ids
            or len(view_ids) != len(set(view_ids))
            or not np.isfinite(matrix).all()
        ):
            raise ValueError(
                "Metric retrieval corpus and embedding rows must match exactly"
            )

        canonical_ids = [str(segment.segment_id) for segment in source_segments]
        if corpus.source_segment_ids != canonical_ids:
            raise ValueError(
                "Metric retrieval corpus does not match canonical report segments"
            )

        generation = uuid.uuid4().hex
        corpus_path = self.embeddings_outputs / (
            f"{file_id}_metric_retrieval_{generation}.json"
        )
        embeddings_path = self.embeddings_outputs / (
            f"{file_id}_metric_retrieval_{generation}.npz"
        )
        old_manifest: Dict[str, Any] = {}
        if manifest_path.exists():
            try:
                old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                old_manifest = {}

        try:
            self._atomic_write_json(
                corpus_path,
                (
                    corpus.model_dump(mode="json")
                    if hasattr(corpus, "model_dump")
                    else corpus.dict()
                ),
            )
            temp_npz = embeddings_path.with_name(
                f".{embeddings_path.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                max_id_length = max([len(value) for value in view_ids] or [1])
                with open(temp_npz, "wb") as handle:
                    np.savez_compressed(
                        handle,
                        embeddings=np.ascontiguousarray(matrix, dtype=np.float32),
                        view_ids=np.asarray(
                            view_ids,
                            dtype=f"<U{max_id_length}",
                        ),
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_npz, embeddings_path)
            finally:
                try:
                    temp_npz.unlink(missing_ok=True)
                except Exception:
                    pass

            manifest = {
                "schema_version": METRIC_CORPUS_SCHEMA_VERSION,
                "generation": generation,
                "file_id": str(file_id),
                "document_id": corpus.document_id,
                "canonical_segment_digest": _canonical_segments_digest(
                    source_segments
                ),
                "corpus_signature": corpus.corpus_signature,
                "chunker_version": corpus.chunker_version,
                "chunker_config": (
                    corpus.config.model_dump(mode="json")
                    if hasattr(corpus.config, "model_dump")
                    else corpus.config.dict()
                ),
                "embedding_model": str(
                    getattr(corpus, "_embedding_model", "") or ""
                ),
                "embedding_dim": int(matrix.shape[1]) if matrix.ndim == 2 else 0,
                "embedding_dtype": str(matrix.dtype),
                "embeddings_normalized": bool(
                    getattr(corpus, "_embeddings_normalized", True)
                ),
                "evidence_block_count": len(corpus.evidence_blocks),
                "retrieval_view_count": len(views),
                "embedding_row_count": int(matrix.shape[0]),
                "view_ids_digest": _ordered_ids_digest(view_ids),
                "corpus_file": corpus_path.name,
                "embeddings_file": embeddings_path.name,
                "saved_at": datetime.now().isoformat(),
            }
            # The manifest is the generation commit marker and is published last.
            self._atomic_write_json(manifest_path, manifest)
        except Exception:
            try:
                corpus_path.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                embeddings_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        # Best-effort cleanup of the previously committed generation only after
        # the new manifest is visible.
        for key in ("corpus_file", "embeddings_file"):
            old_name = str(old_manifest.get(key) or "").strip()
            old_path = self.embeddings_outputs / old_name if old_name else None
            if (
                old_path is not None
                and old_path.parent.resolve() == self.embeddings_outputs.resolve()
                and old_path.name.startswith(f"{file_id}_metric_retrieval_")
                and old_path != corpus_path
                and old_path != embeddings_path
            ):
                try:
                    old_path.unlink(missing_ok=True)
                except Exception:
                    pass

        return {
            "metric_retrieval_manifest_path": str(manifest_path),
            "metric_retrieval_corpus_path": str(corpus_path),
            "metric_retrieval_embeddings_path": str(embeddings_path),
        }

    def load_metric_retrieval_artifacts(
        self,
        file_id: str,
        source_segments: List[TextSegment],
        expected_model: Optional[str] = None,
    ) -> Optional[MetricRetrievalCorpus]:
        """Load and strictly validate a metric sidecar without affecting v1."""
        manifest_path = self._metric_manifest_path(file_id)
        if not manifest_path.exists():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if str(manifest.get("schema_version")) != METRIC_CORPUS_SCHEMA_VERSION:
                raise ValueError("Unsupported metric corpus schema")
            if manifest.get("canonical_segment_digest") != _canonical_segments_digest(
                source_segments
            ):
                raise ValueError("Metric corpus canonical segment digest mismatch")
            stored_model = str(manifest.get("embedding_model") or "")
            if expected_model and stored_model and stored_model != str(expected_model):
                raise ValueError("Metric corpus embedding model mismatch")

            corpus_name = str(manifest.get("corpus_file") or "")
            embeddings_name = str(manifest.get("embeddings_file") or "")
            corpus_path = self.embeddings_outputs / corpus_name
            embeddings_path = self.embeddings_outputs / embeddings_name
            for path in (corpus_path, embeddings_path):
                if (
                    not path.name
                    or path.parent.resolve() != self.embeddings_outputs.resolve()
                    or not path.name.startswith(f"{file_id}_metric_retrieval_")
                    or not path.exists()
                ):
                    raise ValueError("Metric corpus manifest references an invalid payload")

            corpus_json = corpus_path.read_text(encoding="utf-8")
            corpus = (
                MetricRetrievalCorpus.model_validate_json(corpus_json)
                if hasattr(MetricRetrievalCorpus, "model_validate_json")
                else MetricRetrievalCorpus.parse_raw(corpus_json)
            )
            if corpus.corpus_signature != str(manifest.get("corpus_signature") or ""):
                raise ValueError("Metric corpus signature mismatch")
            canonical_ids = [str(segment.segment_id) for segment in source_segments]
            if corpus.source_segment_ids != canonical_ids:
                raise ValueError("Metric corpus source segment order mismatch")

            with np.load(embeddings_path, allow_pickle=False) as payload:
                matrix = np.asarray(payload["embeddings"], dtype=np.float32)
                view_ids = [str(value) for value in payload["view_ids"].tolist()]
            expected_ids = [view.view_id for view in corpus.retrieval_views]
            if (
                matrix.ndim != 2
                or matrix.shape[0] != len(expected_ids)
                or view_ids != expected_ids
                or _ordered_ids_digest(view_ids)
                != str(manifest.get("view_ids_digest") or "")
                or int(manifest.get("embedding_row_count") or -1)
                != matrix.shape[0]
                or int(manifest.get("embedding_dim") or -1)
                != matrix.shape[1]
                or not np.isfinite(matrix).all()
            ):
                raise ValueError("Metric corpus embedding rows are inconsistent")

            object.__setattr__(
                corpus,
                "_embedding_matrix",
                np.ascontiguousarray(matrix, dtype=np.float32),
            )
            object.__setattr__(corpus, "_embedding_view_ids", view_ids)
            object.__setattr__(corpus, "_embedding_model", stored_model)
            object.__setattr__(
                corpus,
                "_embeddings_normalized",
                bool(manifest.get("embeddings_normalized", True)),
            )
            return corpus
        except Exception as error:
            logger.warning(
                f"Failed to load optional metric retrieval corpus for {file_id}: {error}"
            )
            return None

    def delete_report_artifacts(self, file_id: str) -> List[str]:
        """Delete canonical and metric sidecars for exactly one report ID."""
        manifest_path = self._metric_manifest_path(file_id)
        targets = {
            self.embeddings_outputs / f"{file_id}_segments.json",
            self.embeddings_outputs / f"{file_id}_embeddings.npz",
            self.embeddings_outputs / f"{file_id}_embeddings_meta.json",
            manifest_path,
        }
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for key in ("corpus_file", "embeddings_file"):
                    name = str(manifest.get(key) or "").strip()
                    if name:
                        targets.add(self.embeddings_outputs / name)
            except Exception:
                pass
        targets.update(
            self.embeddings_outputs.glob(f"{file_id}_metric_retrieval_*.json")
        )
        targets.update(
            self.embeddings_outputs.glob(f"{file_id}_metric_retrieval_*.npz")
        )

        root = self.embeddings_outputs.resolve()
        removed: List[str] = []
        for path in targets:
            try:
                resolved = path.resolve()
            except Exception:
                continue
            if resolved.parent != root:
                continue
            allowed = (
                resolved.name
                in {
                    f"{file_id}_segments.json",
                    f"{file_id}_embeddings.npz",
                    f"{file_id}_embeddings_meta.json",
                    f"{file_id}_metric_retrieval_manifest.json",
                }
                or resolved.name.startswith(f"{file_id}_metric_retrieval_")
            )
            if not allowed or not resolved.exists() or not resolved.is_file():
                continue
            resolved.unlink()
            removed.append(resolved.name)
        return sorted(set(removed))

    def save_report_artifacts(self, file_id: str, report_content: ReportContent) -> Dict[str, str]:
        """Persist segments + embeddings for fast chat retrieval.

        Files written under: uploads/outputs/embeddings/
          - {file_id}_segments.json
          - {file_id}_embeddings.npz  (float32 matrix)
          - {file_id}_embeddings_meta.json

        Returns:
            dict with paths.
        """
        self.embeddings_outputs.mkdir(parents=True, exist_ok=True)

        segments_path = self.embeddings_outputs / f"{file_id}_segments.json"
        emb_path = self.embeddings_outputs / f"{file_id}_embeddings.npz"
        meta_path = self.embeddings_outputs / f"{file_id}_embeddings_meta.json"

        # 1) segments
        segments = report_content.document_content.segments or []
        segments_payload = [
            {
                "segment_id": s.segment_id,
                "content": s.content,
                "page_number": s.page_number,
                "position_y": s.position_y,
                "segment_type": getattr(s, "segment_type", "text"),
                "position_x": getattr(s, "position_x", None),
                "source_table_id": getattr(s, "source_table_id", None),
                "row_header": getattr(s, "row_header", None),
                "col_header": getattr(s, "col_header", None),
                "value_text": getattr(s, "value_text", None),
                "unit": getattr(s, "unit", None),
                "structure_confidence": getattr(s, "structure_confidence", None),
                "ocr_confidence": getattr(s, "ocr_confidence", None),
                "header_path": getattr(s, "header_path", []),
                "rowspan": getattr(s, "rowspan", 1),
                "colspan": getattr(s, "colspan", 1),
                "parse_pass": getattr(s, "parse_pass", 1),
                "review_status": getattr(s, "review_status", None),
                "conflicts": getattr(s, "conflicts", []),
                "structured_data": getattr(s, "structured_data", None),
            }
            for s in segments
        ]
        segments_path.write_text(json.dumps(segments_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # 2) embeddings
        emb_objs = report_content.embeddings or []
        cached_matrix = getattr(report_content, "_embedding_matrix", None)
        cached_ids = getattr(report_content, "_embedding_segment_ids", None)
        if isinstance(cached_matrix, np.ndarray) and cached_matrix.ndim == 2 and cached_ids is not None and len(cached_ids) == cached_matrix.shape[0]:
            emb_matrix = np.ascontiguousarray(cached_matrix, dtype=np.float32)
            seg_ids = [str(value) for value in cached_ids]
        else:
            seg_ids = [e.segment_id for e in emb_objs]
            emb_matrix = np.asarray([e.embedding for e in emb_objs], dtype=np.float32) if emb_objs else np.zeros((0, 0), dtype=np.float32)
        np.savez_compressed(emb_path, embeddings=emb_matrix, segment_ids=np.array(seg_ids, dtype=object))

        # 3) meta
        meta = {
            "file_id": file_id,
            "document_id": report_content.document_id,
            "content_revision": int(
                getattr(report_content.document_content, "content_revision", 1) or 1
            ),
            "embedding_dim": int(emb_matrix.shape[1]) if emb_matrix.ndim == 2 and emb_matrix.size else 0,
            "segment_count": int(len(segments)),
            "embedding_count": int(len(seg_ids)),
            "saved_at": datetime.now().isoformat(),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # 4) Optional metric-retrieval sidecar. The canonical v1 artifacts above
        # are already complete; a sidecar failure must never invalidate them.
        metric_paths: Dict[str, str] = {}
        metric_corpus = getattr(report_content, "_metric_retrieval_corpus", None)
        if isinstance(metric_corpus, MetricRetrievalCorpus):
            try:
                metric_paths = self.save_metric_retrieval_artifacts(
                    file_id,
                    metric_corpus,
                    list(segments),
                )
            except Exception as error:
                logger.warning(
                    f"Failed to persist optional metric retrieval artifacts for {file_id}: {error}"
                )
        else:
            # A fresh canonical generation must not keep pointing at an older,
            # potentially incompatible metric generation.
            try:
                self._metric_manifest_path(file_id).unlink(missing_ok=True)
            except Exception:
                pass

        # 5) Update file metadata (best-effort)
        try:
            if file_id in self.metadata.get("files", {}):
                info = self.metadata["files"][file_id]
                info["segments_path"] = str(segments_path)
                info["embeddings_path"] = str(emb_path)
                info["embeddings_meta_path"] = str(meta_path)
                if metric_paths:
                    info.update(metric_paths)
                else:
                    info.pop("metric_retrieval_manifest_path", None)
                    info.pop("metric_retrieval_corpus_path", None)
                    info.pop("metric_retrieval_embeddings_path", None)
                self._save_metadata()
        except Exception as e:
            logger.warning(f"Failed to update file metadata with artifact paths for {file_id}: {e}")

        return {
            "segments_path": str(segments_path),
            "embeddings_path": str(emb_path),
            "embeddings_meta_path": str(meta_path),
            **metric_paths,
        }

    def load_report_artifacts(
        self,
        file_id: str,
        *,
        include_metric_corpus: bool = False,
        expected_embedding_model: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Load persisted segments + embeddings.

        Returns None if artifacts are missing.
        """
        # Prefer paths saved in metadata
        info = self.metadata.get("files", {}).get(file_id, {})
        seg_path = Path(info.get("segments_path")) if info.get("segments_path") else (self.embeddings_outputs / f"{file_id}_segments.json")
        emb_path = Path(info.get("embeddings_path")) if info.get("embeddings_path") else (self.embeddings_outputs / f"{file_id}_embeddings.npz")
        meta_path = Path(info.get("embeddings_meta_path")) if info.get("embeddings_meta_path") else (self.embeddings_outputs / f"{file_id}_embeddings_meta.json")

        if not seg_path.exists() or not emb_path.exists():
            return None

        try:
            segments_raw = json.loads(seg_path.read_text(encoding="utf-8"))
            segments: List[TextSegment] = []
            for s in segments_raw:
                if not isinstance(s, dict):
                    continue
                try:
                    segments.append(
                        TextSegment(
                            segment_id=str(s.get("segment_id")),
                            content=str(s.get("content") or ""),
                            page_number=int(s.get("page_number") or 1),
                            position_y=float(s.get("position_y") or 0.0),
                            segment_type=str(s.get("segment_type") or "text"),
                            position_x=float(s.get("position_x")) if s.get("position_x") is not None else None,
                            source_table_id=(str(s.get("source_table_id")) if s.get("source_table_id") is not None else None),
                            row_header=(str(s.get("row_header")) if s.get("row_header") is not None else None),
                            col_header=(str(s.get("col_header")) if s.get("col_header") is not None else None),
                            value_text=(str(s.get("value_text")) if s.get("value_text") is not None else None),
                            unit=(str(s.get("unit")) if s.get("unit") is not None else None),
                            structure_confidence=(float(s.get("structure_confidence")) if s.get("structure_confidence") is not None else None),
                            ocr_confidence=(float(s.get("ocr_confidence")) if s.get("ocr_confidence") is not None else None),
                            header_path=list(s.get("header_path") or []),
                            rowspan=int(s.get("rowspan") or 1),
                            colspan=int(s.get("colspan") or 1),
                            parse_pass=int(s.get("parse_pass") or 1),
                            review_status=(str(s.get("review_status")) if s.get("review_status") is not None else None),
                            conflicts=list(s.get("conflicts") or []),
                            structured_data=(s.get("structured_data") if isinstance(s.get("structured_data"), dict) else None),
                        )
                    )
                except Exception:
                    continue

            data = np.load(emb_path, allow_pickle=True)
            emb_matrix = data.get("embeddings")
            seg_ids = data.get("segment_ids")
            seg_ids = [str(x) for x in (seg_ids.tolist() if seg_ids is not None else [])]

            content_revision = 1
            if meta_path.exists():
                try:
                    artifact_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    content_revision = max(
                        1,
                        int(artifact_meta.get("content_revision", 1) or 1),
                    )
                except Exception:
                    content_revision = 1

            result = {
                "segments": segments,
                "embedding_matrix": emb_matrix,
                "embedding_segment_ids": seg_ids,
                "content_revision": content_revision,
                "segments_path": str(seg_path),
                "embeddings_path": str(emb_path),
            }
            if include_metric_corpus:
                metric_corpus = self.load_metric_retrieval_artifacts(
                    file_id,
                    segments,
                    expected_model=expected_embedding_model,
                )
                result["metric_retrieval_corpus"] = metric_corpus
                if metric_corpus is not None:
                    result["content_revision"] = metric_corpus.content_revision
            return result
        except Exception as e:
            logger.warning(f"Failed to load report artifacts for {file_id}: {e}")
            return None
    
    def list_files_by_type(self, file_type: str, status: Optional[str] = None, 
                          user_id: Optional[int] = None) -> List[Dict]:
        """
        按类型和状态列出文件
        
        Args:
            file_type: 文件类型
            status: 文件状态（可选）
            user_id: 用户ID (如果提供,只返回该用户的文件)
            
        Returns:
            文件信息列表
        """
        files = []
        updated_any = False
        for file_id, file_info in self.metadata["files"].items():
            # 检查文件类型
            if file_info["file_type"] != file_type:
                continue
            
            # 检查状态
            if status is not None and file_info["status"] != status:
                continue
            
            # 检查用户ID
            if user_id is not None:
                file_user_id = file_info.get("user_id")
                # Backward-compatibility:
                # - Historical runs may have user_id omitted (None).
                # - For those "public" files, allow listing for any logged-in user.
                # This prevents dashboards from becoming empty after auth/user resets.
                if file_user_id is not None and file_user_id != user_id:
                    continue
            
            files.append(file_info)

            # Backfill page_count for legacy uploads (dashboard "No. of Pages").
            try:
                if (
                    file_type == "report"
                    and (file_info.get("page_count") is None)
                    and str(file_info.get("file_path", "")).lower().endswith(".pdf")
                ):
                    pc = _safe_pdf_page_count_from_path(Path(file_info["file_path"]))
                    if pc is not None:
                        file_info["page_count"] = pc
                        updated_any = True
            except Exception:
                # Never block listing due to a best-effort enrichment.
                pass
        
        # 按上传时间排序
        files.sort(key=lambda x: x["upload_time"], reverse=True)

        if updated_any:
            self._save_metadata()
        return files
    
    def list_user_files(self, user_id: int, file_type: Optional[str] = None, 
                       status: Optional[str] = None) -> List[Dict]:
        """
        列出指定用户的所有文件
        
        Args:
            user_id: 用户ID
            file_type: 文件类型过滤 (可选)
            status: 文件状态过滤 (可选)
            
        Returns:
            文件信息列表
        """
        files = []
        updated_any = False
        for file_id, file_info in self.metadata["files"].items():
            # 检查用户ID
            file_user_id = file_info.get("user_id")
            # Same compatibility as list_files_by_type:
            # treat missing user_id as public so the user can still see old data.
            if file_user_id is not None and file_user_id != user_id:
                continue
            
            # 检查文件类型
            if file_type is not None and file_info["file_type"] != file_type:
                continue
            
            # 检查状态
            if status is not None and file_info["status"] != status:
                continue
            
            files.append(file_info)

            # Backfill page_count for legacy uploads.
            try:
                if (
                    (file_type is None or file_type == "report")
                    and (file_info.get("file_type") == "report")
                    and (file_info.get("page_count") is None)
                    and str(file_info.get("file_path", "")).lower().endswith(".pdf")
                ):
                    pc = _safe_pdf_page_count_from_path(Path(file_info["file_path"]))
                    if pc is not None:
                        file_info["page_count"] = pc
                        updated_any = True
            except Exception:
                pass
        
        # 按上传时间排序
        files.sort(key=lambda x: x["upload_time"], reverse=True)

        if updated_any:
            self._save_metadata()
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
                
                if str(file_info.get("file_type") or "").lower() == "report":
                    try:
                        self.delete_report_artifacts(str(file_id))
                    except Exception as artifact_error:
                        logger.warning(
                            "Failed to clean report retrieval artifacts for "
                            f"{file_id}: {artifact_error}"
                        )

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
