"""
ESG智能聊天机器人后端模块
"""

import json
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
import openai
from loguru import logger
import numpy as np

from ..shared_embedding_model import encode_query_texts, get_shared_embedding_model
from ..embedding_settings import get_configured_embedding_model_name
from ..models import (
    ProcessingConfig,
    ChatMessage,
    ChatSession,
    ChatRequest,
    ChatResponse,
    ReportContent,
    ComplianceAssessment,
    DisclosureStatus
)


class ESGChatbot:
    """交互式ESG聊天机器人"""
    
    def __init__(self, config: ProcessingConfig):
        """
        初始化聊天机器人
        
        Args:
            config: 处理配置
        """
        self.config = config
        self.llm_client = self._init_llm_client()
        
        
        # 原缓存内对话实例
        # 加入ChatID后，session会由上游API自由添加
        self.sessions: Dict[str, ChatSession] = {}
        self.report_content: Optional[ReportContent] = None
        self.compliance_assessment: Optional[ComplianceAssessment] = None

        # Retrieval caches (built on load_context)
        self._segment_map: Dict[str, Any] = {}
        self._embedding_matrix: Optional[np.ndarray] = None
        self._embedding_segment_ids: List[str] = []
        self._embedder_model = None  # SentenceTransformer (shared by API startup)

    def set_embedding_model(self, model) -> None:
        """Inject embedding model (SentenceTransformer) to avoid duplicate loading."""
        self._embedder_model = model
        

    def _ensure_embedder_model(self):
        """Lazily load embedding model for chat semantic retrieval."""
        if self._embedder_model is not None:
            return self._embedder_model
        try:
            import os
            device = os.getenv("LOCAL_EMBEDDINGS_DEVICE") or str(getattr(self.config, "device", "cuda") or "cuda")
            repo_id = str(getattr(self.config, "embedding_model", "") or get_configured_embedding_model_name())
            self._embedder_model = get_shared_embedding_model(
                repo_id,
                device=device,
                hf_home=os.getenv("HF_HOME", "/root/.cache/huggingface"),
                trust_remote_code=True,
            )
            logger.info(f"Chat embedding model loaded lazily, device={device}")
        except Exception as exc:
            logger.warning(f"Chat lazy embedding model load failed: {exc}")
            self._embedder_model = None
        return self._embedder_model

    def _init_llm_client(self):
        """初始化LLM客户端"""
        if not self.config.llm_api_key:
            raise ValueError("LLM API key is required for chatbot. Please configure LLM_API_KEY in your .env file.")

        client = openai.OpenAI(
            api_key=self.config.llm_api_key,
            base_url=self.config.llm_base_url if self.config.llm_base_url else "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        logger.info("Chatbot LLM client initialized successfully")
        return client
    
    def load_context(
        self, 
        report_content: Optional[ReportContent] = None,
        compliance_assessment: Optional[ComplianceAssessment] = None
    ):
        """
        加载报告和合规评估上下文
        
        Args:
            report_content: 报告内容
            compliance_assessment: 合规评估结果
        """
        if report_content:
            self.report_content = report_content
            logger.info(f"Loaded report context: {report_content.document_id}")

            # Build fast lookup maps once
            try:
                self._segment_map = {s.segment_id: s for s in (report_content.document_content.segments or [])}
            except Exception:
                self._segment_map = {}

            # Load embedding matrix either from attached cache (preferred) or from ReportContent.embeddings
            self._embedding_matrix = None
            self._embedding_segment_ids = []
            try:
                mat = getattr(report_content, "_embedding_matrix", None)
                ids = getattr(report_content, "_embedding_segment_ids", None)
                if mat is not None and ids is not None:
                    self._embedding_matrix = np.asarray(mat, dtype=np.float32)
                    self._embedding_segment_ids = [str(x) for x in list(ids)]
                elif getattr(report_content, "embeddings", None):
                    ids2 = [e.segment_id for e in report_content.embeddings]
                    mat2 = np.asarray([e.embedding for e in report_content.embeddings], dtype=np.float32)
                    self._embedding_matrix = mat2
                    self._embedding_segment_ids = ids2

                # Filter to segments that actually exist
                if self._embedding_matrix is not None and self._embedding_segment_ids:
                    keep = [i for i, sid in enumerate(self._embedding_segment_ids) if sid in self._segment_map]
                    if keep and len(keep) != len(self._embedding_segment_ids):
                        self._embedding_matrix = self._embedding_matrix[keep]
                        self._embedding_segment_ids = [self._embedding_segment_ids[i] for i in keep]

                # Normalize for fast cosine via dot product
                if self._embedding_matrix is not None and self._embedding_matrix.size:
                    norms = np.linalg.norm(self._embedding_matrix, axis=1, keepdims=True)
                    norms[norms == 0] = 1.0
                    self._embedding_matrix = self._embedding_matrix / norms
            except Exception as e:
                logger.warning(f"Failed to build embedding cache: {e}")
                self._embedding_matrix = None
                self._embedding_segment_ids = []
            
        if compliance_assessment:
            self.compliance_assessment = compliance_assessment
            logger.info(f"Loaded compliance assessment for {compliance_assessment.total_metrics_analyzed} metrics")
    
    # NEW FUNCTION
    def restore_session(self, session_id: str, history_data: List[Dict[str, Any]]) -> str:
        """
        根据缓存或本地加载的对话历史重建对话实例
        新的架构会使用本地存储来实现persistence

        Args:
            session_id: 会话ID
            history_data: 对话历史，由上游api加载对应ID的历史
            
        Returns:
            str: 会话ID
        """
        restored_messages = []
        for msg_data in history_data:
            # Handle string timestamp back to datetime object
            timestamp = msg_data.get("timestamp")
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp)
                except ValueError:
                    timestamp = datetime.now()
            else:
                timestamp = datetime.now()

            restored_messages.append(ChatMessage(
                role=msg_data.get("role", "user"),
                content=msg_data.get("content", ""),
                timestamp=timestamp
            ))

        session = ChatSession(
            session_id=session_id,
            report_context=self.report_content.document_id if self.report_content else None,
            compliance_context=self.compliance_assessment.report_id if self.compliance_assessment else None,
            messages=restored_messages
        )
        
        self.sessions[session_id] = session
        logger.info(f"Restored session {session_id} with {len(restored_messages)} messages")
        return session_id

    def create_session(
        self,
        session_id: Optional[str] = None,
        *,
        include_report_context: bool = True,
    ) -> str:
        """
        创建新的聊天会话
        
        Args:
            session_id: 会话ID（可选）
            
        Returns:
            str: 会话ID
        """
        if not session_id:
            session_id = str(uuid.uuid4())
        
        session = ChatSession(
            session_id=session_id,
            report_context=(
                self.report_content.document_id
                if include_report_context and self.report_content
                else None
            ),
            compliance_context=(
                self.compliance_assessment.report_id
                if include_report_context and self.compliance_assessment
                else None
            ),
            messages=[]
        )
        
        self.sessions[session_id] = session
        logger.info(f"Created chat session: {session_id}")
        return session_id
    
    def chat(self, request: ChatRequest) -> ChatResponse:
        """
        处理聊天请求
        假设load_context()和restore_session()已经被上游API trigger
        
        Args:
            request: 聊天请求
            
        Returns:
            ChatResponse: 聊天响应
        """
        # Resolve Session
        session_id = request.session_id
        if not session_id:
            session_id = self.create_session(
                include_report_context=request.include_context,
            )
        
        if session_id not in self.sessions:
            self.create_session(
                session_id,
                include_report_context=request.include_context,
            )

        # Never reuse a report-bound conversation as a general chat session.
        # Otherwise its prior answers could reintroduce report details through
        # conversation history even though include_context is false.
        existing_session = self.sessions[session_id]
        if not request.include_context and (
            existing_session.report_context
            or existing_session.compliance_context
        ):
            session_id = self.create_session(include_report_context=False)
            
        session = self.sessions[session_id]
        if request.include_context:
            # A session may have been created before report context became
            # available. Tag it as soon as a contextual answer can be produced
            # so a later general-mode request cannot reuse that history.
            session.report_context = (
                self.report_content.document_id if self.report_content else None
            )
            session.compliance_context = (
                self.compliance_assessment.report_id
                if self.compliance_assessment
                else None
            )
        
        # 加载对话
        user_message = ChatMessage(
            role="user",
            content=request.message,
            timestamp=datetime.now()
        )
        session.messages.append(user_message)
        
        # Analyze & Retrieve
        question_type = self._analyze_question_type(request.message)
        
        relevant_segments = []
        relevant_content_text = []
        
        if request.include_context and self.report_content:
            relevant_segments = self._search_relevant_content(request.message)
            relevant_content_text = self._get_segments_content(relevant_segments[:5])
        
        # Generate LLM Response
        # We pass the *entire* history (including the msg we just added) to the LLM context builder
        # Optional UI context (Cross Analysis etc.)
        context_payload = getattr(request, "context", None)
        response_text = self._generate_llm_response(
            question=request.message,
            question_type=question_type,
            relevant_content=relevant_content_text,
            conversation_history=session.messages[-10:], # Keep context window manageable
            context_payload=context_payload,
            include_report_context=request.include_context,
        )
        
        assistant_message = ChatMessage(
            role="assistant",
            content=response_text,
            timestamp=datetime.now()
        )
        session.messages.append(assistant_message)
        session.updated_at = datetime.now()
        
        return ChatResponse(
            session_id=session.session_id,
            response=response_text,
            relevant_segments=relevant_segments[:3]
        )
            
    def _analyze_question_type(self, question: str) -> str:
        """
        分析问题类型
        
        Args:
            question: 用户问题
            
        Returns:
            str: 问题类型
        """
        question_lower = question.lower()
        
        # Define question type keywords
        if any(word in question_lower for word in ["what is", "explain", "definition", "meaning", "define"]):
            return "definition"
        elif any(word in question_lower for word in ["how much", "data", "number", "value", "specific", "score", "percentage"]):
            return "data_query"
        elif any(word in question_lower for word in ["summary", "summarize", "overview", "main", "overall"]):
            return "summary"
        elif any(word in question_lower for word in ["compliance", "disclosure", "disclosed", "compliant", "whether"]):
            return "compliance"
        elif any(word in question_lower for word in ["advice", "how to", "suggest", "recommendation", "improve"]):
            return "advice"
        else:
            return "general"
    
    def _search_relevant_content(self, query: str) -> List[str]:
        """
        搜索与问题相关的内容段落
        
        Args:
            query: 查询问题
            
        Returns:
            List[str]: 相关段落ID列表
        """
        if not self.report_content:
            return []

        # 1) Semantic vector retrieval (preferred)
        if self._embedding_matrix is not None and self._embedding_matrix.size:
            model = self._ensure_embedder_model()
            if model is None:
                return []
            try:
                q_vec = encode_query_texts(model, [query], normalize_embeddings=True, show_progress_bar=False)
                q = np.asarray(q_vec[0], dtype=np.float32)
                # Cosine similarity via dot product (matrix normalized in load_context)
                sims = self._embedding_matrix @ q
                k = min(10, sims.shape[0])
                if k <= 0:
                    return []
                top_idx = np.argpartition(-sims, k - 1)[:k]
                top_idx = top_idx[np.argsort(-sims[top_idx])]
                return [self._embedding_segment_ids[i] for i in top_idx]
            except Exception as e:
                logger.warning(f"Semantic retrieval failed, fallback to keyword: {e}")

        # 2) Keyword fallback (robust)
        relevant_segments: List[str] = []
        query_lower = (query or "").lower()
        keywords = [k for k in query_lower.split() if k]
        for seg in (self.report_content.document_content.segments or []):
            text = (seg.content or "").lower()
            if any(k in text for k in keywords):
                relevant_segments.append(seg.segment_id)
                if len(relevant_segments) >= 10:
                    break
        return relevant_segments
    
    def _get_segments_content(self, segment_ids: List[str]) -> List[str]:
        """
        获取段落内容
        
        Args:
            segment_ids: 段落ID列表
            
        Returns:
            List[str]: 段落内容列表
        """
        if not self.report_content:
            return []
        
        contents: List[str] = []
        for segment_id in segment_ids:
            seg = self._segment_map.get(segment_id)
            if seg is not None:
                contents.append(f"[{segment_id} - Page {seg.page_number}]\n{seg.content}")
        
        return contents
    
    def _generate_llm_response(
        self,
        question: str,
        question_type: str,
        relevant_content: List[str],
        conversation_history: List[ChatMessage],
        context_payload: Optional[dict] = None,
        include_report_context: bool = True,
    ) -> str:
        """
        使用LLM生成回复
        
        Args:
            question: 用户问题
            question_type: 问题类型
            relevant_content: 相关内容
            conversation_history: 对话历史
            
        Returns:
            str: 回复文本
        """
        # 构建提示词
        prompt = self._build_chat_prompt(
            question,
            question_type,
            relevant_content,
            conversation_history,
            context_payload=context_payload,
            include_report_context=include_report_context,
        )

        system_message = (
            "You are an ESG analyst. Use ONLY the provided report segments as "
            "evidence. Cite sources as [SEGID p#] for each key claim. If evidence "
            "is missing, say you cannot find it in the report."
            if include_report_context
            else (
                "You are a general ESG assistant. Answer ESG questions clearly "
                "using general knowledge. Do not claim that you reviewed or have "
                "access to a specific report unless report evidence is explicitly "
                "provided in this request."
            )
        )
        
        try:
            
            # 调用LLM
            response = self.llm_client.chat.completions.create(
                model=self.config.llm_model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=1000
            )
            
            response_text = response.choices[0].message.content
            
        except Exception as e:
            error_str = str(e)
            logger.error(f"LLM generation failed: {e}")
            
            # 检查是否是模型访问被拒绝的错误
            if "403" in error_str or "AccessDenied" in error_str or "Unpurchased" in error_str:
                error_message = (
                    f"LLM模型访问被拒绝。当前配置的模型 '{self.config.llm_model}' 可能不可用或需要购买。\n\n"
                    f"请检查并修改 `backend/config/.env` 文件中的 `LLM_MODEL` 配置：\n"
                    f"- 尝试使用 'qwen-plus' 或 'qwen-turbo'（这些模型通常更容易访问）\n"
                    f"- 确保您的API密钥有权限访问所选模型\n"
                    f"- 如果问题持续，请联系API服务提供商确认模型访问权限"
                )
                logger.error(error_message)
                raise RuntimeError(error_message)
            else:
                raise RuntimeError(f"LLM response generation error: {e}")

        return response_text
    
    def _build_chat_prompt(
        self,
        question: str,
        question_type: str,
        relevant_content: List[str],
        conversation_history: List[ChatMessage],
        context_payload: Optional[dict] = None,
        include_report_context: bool = True,
    ) -> str:
        """
        构建聊天提示词
        
        Args:
            question: 用户问题
            question_type: 问题类型
            relevant_content: 相关内容
            conversation_history: 对话历史
            
        Returns:
            str: 提示词
        """
        prompt = ""

        # Inject structured UI context (if provided) in a compact, research-friendly way.
        # This is intentionally short so it doesn't crowd out evidence and history.
        if context_payload:
            try:
                ids = context_payload.get("ids") if isinstance(context_payload, dict) else None
                dim = context_payload.get("dimension") if isinstance(context_payload, dict) else None
                topic = context_payload.get("topic") if isinstance(context_payload, dict) else None
                metric = context_payload.get("metric") if isinstance(context_payload, dict) else None

                ctx_lines = []
                if ids:
                    ctx_lines.append(f"selected_reports={ids}")
                if dim:
                    ctx_lines.append(f"dimension={dim}")
                if topic:
                    ctx_lines.append(f"topic={topic}")
                if metric:
                    ctx_lines.append(f"metric={metric}")

                if ctx_lines:
                    prompt += "Context (UI): " + ", ".join(ctx_lines) + "\n\n"
            except Exception:
                # Never fail the chat request due to context formatting.
                pass

        prompt += f"User question: {question}\n\n"
        
        # 如果没有数据，添加提示
        if not include_report_context:
            prompt += (
                "Note: This is a general ESG conversation without report or "
                "compliance-assessment context. Do not infer or mention any "
                "previously loaded report.\n\n"
            )
        elif not self.compliance_assessment and not self.report_content:
            prompt += "Note: No ESG report has been uploaded and analyzed yet. Please answer the user's question about ESG topics in general, and remind them that for specific report analysis, they need to upload a report first.\n\n"
        
        # 添加报告背景信息
        if include_report_context and self.compliance_assessment:
            fully_disclosed = self.compliance_assessment.disclosure_summary.get("fully_disclosed", 0)
            partially_disclosed = self.compliance_assessment.disclosure_summary.get("partially_disclosed", 0)
            not_disclosed = self.compliance_assessment.disclosure_summary.get("not_disclosed", 0)
            
            prompt += f"""Report Background Information:
- Report ID: {self.compliance_assessment.report_id}
- Total Analyzed Metrics: {self.compliance_assessment.total_metrics_analyzed}
- Overall Compliance Score: {self.compliance_assessment.overall_compliance_score:.1%}
- Disclosed: {fully_disclosed} metrics ({f"{fully_disclosed/self.compliance_assessment.total_metrics_analyzed*100:.1f}%" if self.compliance_assessment.total_metrics_analyzed else "N/A"})
- Partially Disclosed: {partially_disclosed} metrics ({f"{partially_disclosed/self.compliance_assessment.total_metrics_analyzed*100:.1f}%" if self.compliance_assessment.total_metrics_analyzed else "N/A"})
- Not Disclosed: {not_disclosed} metrics ({f"{not_disclosed/self.compliance_assessment.total_metrics_analyzed*100:.1f}%" if self.compliance_assessment.total_metrics_analyzed else "N/A"})

Key Metric Analysis Examples:
"""
            # 添加一些具体的指标分析作为上下文
            if hasattr(self.compliance_assessment, 'metric_analyses') and self.compliance_assessment.metric_analyses:
                for i, analysis in enumerate(self.compliance_assessment.metric_analyses[:3]):  # 展示前3个作为样例
                    status_text = {
                        "fully_disclosed": "Disclosed",
                        "partially_disclosed": "Partially Disclosed", 
                        "not_disclosed": "Not Disclosed"
                    }
                    status = getattr(analysis, 'disclosure_status', 'not_disclosed')
                    if isinstance(status, str):
                        status_display = status_text.get(status, status)
                    else:
                        status_display = str(status)
                        
                    metric_name = getattr(analysis, 'metric_name', 'Unknown')
                    metric_id = getattr(analysis, 'metric_id', 'Unknown')
                    reasoning = getattr(analysis, 'reasoning', '')[:200]  # 限制长度
                    
                    prompt += f"- {metric_name} ({metric_id}): {status_display}\n  Analysis: {reasoning}...\n\n"
            
            prompt += "\n"
        
        # 添加相关内容
        if include_report_context and relevant_content:
            prompt += "Relevant Report Content:\n"
            for i, content in enumerate(relevant_content, 1):
                prompt += f"\nSegment {i}:\n{content}\n"
            prompt += "\n"
        
        # 添加对话历史（最近3轮）
        if len(conversation_history) > 1:
            prompt += "Recent Conversation History:\n"
            for msg in conversation_history[-6:-1]:  # 排除当前消息
                if msg.role == "user":
                    prompt += f"User: {msg.content}\n"
                else:
                    prompt += f"Assistant: {msg.content[:200]}...\n"
            prompt += "\n"
        
        # Add guidance that matches the request mode. General homepage chat must
        # never inherit report-only citation requirements.
        if include_report_context:
            if question_type == "definition":
                prompt += "Please provide clear definitions and explanations, including relevant ESG standards."
            elif question_type == "data_query":
                prompt += "Please search for specific data from the relevant content, and clearly indicate the source page if found."
            elif question_type == "summary":
                prompt += "Please provide a concise summary highlighting key information."
            elif question_type == "compliance":
                prompt += "Please answer based on compliance assessment results, explaining disclosure status and relevant evidence."
            elif question_type == "advice":
                prompt += "Please provide professional advice and improvement recommendations."
            else:
                prompt += "Please provide accurate and professional answers."

            prompt += "\n\nIf there is specific page information in the content, please point it out in your answer."
            prompt += """

Answer Requirements:
- Ground every key claim in the provided segments.
- For each key claim, cite at least one source as [SEGID p#].
- If the report does not contain the requested information, explicitly say so.
- Do not invent numbers, targets, or policies.
"""
        else:
            if question_type == "definition":
                prompt += "Please provide a clear general definition and explain any relevant ESG standards."
            elif question_type == "data_query":
                prompt += "Please explain generally available ESG data concepts and state any uncertainty or knowledge limitations."
            elif question_type == "summary":
                prompt += "Please provide a concise general summary highlighting the key information."
            elif question_type == "compliance":
                prompt += "Please explain the compliance concept generally without implying that a specific report was assessed."
            elif question_type == "advice":
                prompt += "Please provide practical, professional ESG advice and improvement recommendations."
            else:
                prompt += "Please provide an accurate and professional general ESG answer."

            prompt += """

Answer Requirements:
- Answer from general ESG knowledge only.
- Clearly distinguish established guidance from examples or assumptions.
- Do not claim to have reviewed a specific report.
- Do not invent numbers, targets, policies, or citations.
"""

        return prompt
    
    def get_session_history(self, session_id: str) -> Optional[List[ChatMessage]]:
        """
        获取会话历史
        
        Args:
            session_id: 会话ID
            
        Returns:
            Optional[List[ChatMessage]]: 消息历史
        """
        if session_id in self.sessions:
            return self.sessions[session_id].messages
        return None

    def get_session_history_as_dict(self, session_id: str) -> List[Dict]:
        """Helper to return history in a JSON-serializable format for the API"""
        if session_id in self.sessions:
            return [
                {
                    "role": msg.role, 
                    "content": msg.content, 
                    "timestamp": msg.timestamp.isoformat()
                } 
                for msg in self.sessions[session_id].messages
            ]
        return []
    
    def clear_session(self, session_id: str) -> bool:
        """
        清除会话
        
        Args:
            session_id: 会话ID
            
        Returns:
            bool: 是否成功
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
            logger.info(f"Cleared session: {session_id}")
            return True
        return False
    
    def export_session(self, session_id: str) -> Optional[Dict]:
        """
        导出会话数据
        
        Args:
            session_id: 会话ID
            
        Returns:
            Optional[Dict]: 会话数据
        """
        if session_id not in self.sessions:
            return None
        
        session = self.sessions[session_id]
        return {
            "session_id": session.session_id,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat()
                }
                for msg in session.messages
            ],
            "report_context": session.report_context,
            "compliance_context": session.compliance_context
        }
