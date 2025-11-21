"""
ESG智能聊天机器人后端模块
"""

import json
import uuid
from typing import List, Optional, Dict
from datetime import datetime
import openai
from loguru import logger
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from .models import (
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
        self.sessions: Dict[str, ChatSession] = {}
        self.report_content: Optional[ReportContent] = None
        self.compliance_assessment: Optional[ComplianceAssessment] = None
        
    def _init_llm_client(self):
        """初始化LLM客户端"""
        if not self.config.llm_api_key:
            raise ValueError("LLM API key is required for chatbot. Please configure LLM_API_KEY in your .env file.")

        client = openai.OpenAI(
            api_key=self.config.llm_api_key,
            base_url=self.config.llm_base_url if self.config.llm_base_url else "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
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
            
        if compliance_assessment:
            self.compliance_assessment = compliance_assessment
            logger.info(f"Loaded compliance assessment for {compliance_assessment.total_metrics_analyzed} metrics")
    
    def create_session(self, session_id: Optional[str] = None) -> str:
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
            report_context=self.report_content.document_id if self.report_content else None,
            compliance_context=self.compliance_assessment.report_id if self.compliance_assessment else None
        )
        
        self.sessions[session_id] = session
        logger.info(f"Created chat session: {session_id}")
        return session_id
    
    def chat(self, request: ChatRequest) -> ChatResponse:
        """
        处理聊天请求
        
        Args:
            request: 聊天请求
            
        Returns:
            ChatResponse: 聊天响应
        """
        # 获取或创建会话
        if request.session_id and request.session_id in self.sessions:
            session = self.sessions[request.session_id]
        else:
            session_id = self.create_session(request.session_id)
            session = self.sessions[session_id]
        
        # 添加用户消息到历史
        user_message = ChatMessage(
            role="user",
            content=request.message
        )
        session.messages.append(user_message)
        
        # 分析问题类型
        question_type = self._analyze_question_type(request.message)
        logger.info(f"Question type: {question_type}")
        
        # 搜索相关内容
        relevant_segments = []
        relevant_content = []
        
        if request.include_context and self.report_content:
            relevant_segments = self._search_relevant_content(request.message)
            relevant_content = self._get_segments_content(relevant_segments[:5])
        
        # 生成回复
        response_text = self._generate_llm_response(
            request.message,
            question_type,
            relevant_content,
            session.messages[-10:]  # 使用最近10条消息作为上下文
        )
        
        # 添加助手消息到历史
        assistant_message = ChatMessage(
            role="assistant",
            content=response_text
        )
        session.messages.append(assistant_message)
        session.updated_at = datetime.now()
        
        # 创建响应
        response = ChatResponse(
            session_id=session.session_id,
            response=response_text,
            relevant_segments=relevant_segments[:3]
        )
        
        return response
    
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
        if not self.report_content or not self.report_content.embeddings:
            return []
        
        relevant_segments = []
        query_lower = query.lower()
        
        # 关键词搜索
        for segment in self.report_content.document_content.segments:
            if any(keyword in segment.content.lower() for keyword in query_lower.split()):
                relevant_segments.append(segment.segment_id)
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
        
        contents = []
        for segment_id in segment_ids:
            for segment in self.report_content.document_content.segments:
                if segment.segment_id == segment_id:
                    contents.append(f"[{segment_id} - Page {segment.page_number}]\n{segment.content}")
                    break
        
        return contents
    
    def _generate_llm_response(
        self,
        question: str,
        question_type: str,
        relevant_content: List[str],
        conversation_history: List[ChatMessage]
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
            conversation_history
        )
        
        try:
            # 调用LLM
            response = self.llm_client.chat.completions.create(
                model=self.config.llm_model,
                messages=[
                    {"role": "system", "content": "You are a professional ESG consultant assistant helping users understand and analyze ESG report content. Please answer questions using professional, accurate, and friendly language."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            
            response_text = response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            raise RuntimeError(f"LLM response generation error: {e}")

        return response_text
    
    def _build_chat_prompt(
        self,
        question: str,
        question_type: str,
        relevant_content: List[str],
        conversation_history: List[ChatMessage]
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
        prompt = f"User question: {question}\n\n"
        
        # 添加报告背景信息
        if self.compliance_assessment:
            fully_disclosed = self.compliance_assessment.disclosure_summary.get("fully_disclosed", 0)
            partially_disclosed = self.compliance_assessment.disclosure_summary.get("partially_disclosed", 0)
            not_disclosed = self.compliance_assessment.disclosure_summary.get("not_disclosed", 0)
            
            prompt += f"""Report Background Information:
- Report ID: {self.compliance_assessment.report_id}
- Total Analyzed Metrics: {self.compliance_assessment.total_metrics_analyzed}
- Overall Compliance Score: {self.compliance_assessment.overall_compliance_score:.1%}
- Fully Disclosed: {fully_disclosed} metrics ({fully_disclosed/self.compliance_assessment.total_metrics_analyzed*100:.1f}%)
- Partially Disclosed: {partially_disclosed} metrics ({partially_disclosed/self.compliance_assessment.total_metrics_analyzed*100:.1f}%)
- Not Disclosed: {not_disclosed} metrics ({not_disclosed/self.compliance_assessment.total_metrics_analyzed*100:.1f}%)

Key Metric Analysis Examples:
"""
            # 添加一些具体的指标分析作为上下文
            if hasattr(self.compliance_assessment, 'metric_analyses') and self.compliance_assessment.metric_analyses:
                for i, analysis in enumerate(self.compliance_assessment.metric_analyses[:3]):  # 展示前3个作为样例
                    status_text = {
                        "fully_disclosed": "Fully Disclosed",
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
        if relevant_content:
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
        
        # Add specific guidance based on question type
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