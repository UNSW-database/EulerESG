"""Chat service functions."""

from .common import *  # noqa: F401,F403
from ..gpu_model_lifecycle import with_backend_model_task


async def get_file_chat_history(file_id: str, user_id: int = Depends(get_current_user)):
    """
    Get persistent chat history for a specific file (只能访问自己的文件)
    """
    # 检查文件是否属于当前用户
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")
    
    history = _load_chat_history(file_id)
    return {
        "file_id": file_id,
        "messages": history
    }


@with_backend_model_task("chat_with_file")
async def chat_with_file(
    file_id: str, 
    request: ChatRequest,
    user_id: int = Depends(get_current_user)
):
    # 检查文件是否属于当前用户
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")
    
    chatbot = system_components["chatbot"]

    history_list = _load_chat_history(file_id)  # Returns List[Dict]
    assessment, report_content = _load_specific_report_context(file_id)

    with _chatbot_ops_lock:
        chatbot.load_context(report_content, assessment)
        chatbot.restore_session(session_id=file_id, history_data=history_list)
        request.session_id = file_id
        response = chatbot.chat(request)
        updated_history = chatbot.get_session_history_as_dict(file_id)

    _save_chat_history(file_id, updated_history)

    return response


async def clear_file_chat(file_id: str, user_id: int = Depends(get_current_user)):
    """Clear chat history for a specific file (只能删除自己的文件)"""
    try:
        # 检查文件是否属于当前用户
        file_info = file_manager.get_file_info(file_id, user_id=user_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found or access denied")
        history_path = _get_chat_history_path(file_id)
        if history_path.exists():
            history_path.unlink() # Delete the file
            
        # Also clear from memory if this is the active file
        # ...
        
        return {"status": "success", "message": "Chat history cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@with_backend_model_task("chat")
async def chat(request: ChatRequest):
    """
    处理聊天请求
    
    Args:
        request: 聊天请求
        
    Returns:
        ChatResponse: 聊天响应
    """
    try:
        chatbot = system_components["chatbot"]

        # General chat must be request-local. Do not load or expose the
        # process-wide latest report merely because another page used the shared
        # chatbot first.
        if not request.include_context:
            with _chatbot_ops_lock:
                return chatbot.chat(request)
        
        # 优先使用内存中的数据（如果存在）
        latest_assessment = system_components.get("current_assessment")
        report_content = system_components.get("current_report")
        
        logger.debug(f"Chat memory state: assessment={latest_assessment is not None}, report={report_content is not None}")
        
        # 如果内存中没有数据，尝试从文件系统加载
        if not latest_assessment:
            logger.debug("No assessment in memory; loading from files")
            latest_assessment = _load_latest_assessment_for_chat()
            logger.debug(f"Assessment loaded from files: {latest_assessment is not None}")

        if not report_content:
            logger.debug("No report content in memory; loading from files")
            report_content = _load_report_content_for_chat()
            logger.debug(f"Report content loaded from files: {report_content is not None}")

        with _chatbot_ops_lock:
            # 如果没有数据，仍然允许聊天，但只能回答一般性问题
            if not latest_assessment and not report_content:
                logger.warning(
                    "No analysis data available for chat. Chatbot will work in general mode only."
                )
            elif latest_assessment and report_content:
                enhanced_content = _create_enhanced_knowledge_base(
                    latest_assessment, report_content
                )
                chatbot.load_context(
                    compliance_assessment=latest_assessment,
                    report_content=enhanced_content if enhanced_content else report_content,
                )
                segments_count = (
                    len(enhanced_content.document_content.segments)
                    if enhanced_content and hasattr(enhanced_content, "document_content")
                    else (
                        len(report_content.document_content.segments)
                        if report_content
                        and hasattr(report_content, "document_content")
                        else 0
                    )
                )
                logger.info(
                    f"Loaded enhanced knowledge base: {latest_assessment.total_metrics_analyzed} metrics + {segments_count} content segments"
                )
            elif latest_assessment:
                chatbot.load_context(compliance_assessment=latest_assessment)
                logger.info(
                    f"Loaded assessment data: {latest_assessment.total_metrics_analyzed} metrics"
                )
            elif report_content:
                chatbot.load_context(report_content=report_content)
                segments_count = (
                    len(report_content.document_content.segments)
                    if hasattr(report_content, "document_content")
                    and report_content.document_content
                    else 0
                )
                logger.info(f"Loaded report content: {segments_count} segments")

            response = chatbot.chat(request)
        return response
        
    except HTTPException:
        raise
    except RuntimeError as e:
        # 如果是LLM访问错误，返回更友好的错误信息
        error_msg = str(e)
        if "LLM模型访问被拒绝" in error_msg or "AccessDenied" in error_msg:
            logger.error(f"LLM access denied error in chat: {e}")
            raise HTTPException(
                status_code=503,  # Service Unavailable - 更合适的错误码
                detail=error_msg
            )
        else:
            logger.error(f"Runtime error in chat: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Error in chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_chat_history(session_id: str):
    """
    获取聊天历史
    
    Args:
        session_id: 会话ID
        
    Returns:
        聊天历史
    """
    chatbot = system_components["chatbot"]
    with _chatbot_ops_lock:
        history = chatbot.get_session_history(session_id)

    if not history:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "session_id": session_id,
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat()
            }
            for msg in history
        ]
    }


async def clear_chat_session(session_id: str):
    """
    清除聊天会话
    
    Args:
        session_id: 会话ID
        
    Returns:
        操作结果
    """
    chatbot = system_components["chatbot"]
    with _chatbot_ops_lock:
        success = chatbot.clear_session(session_id)

    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"status": "success", "message": f"Session {session_id} cleared"}
