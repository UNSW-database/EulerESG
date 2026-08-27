from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from esg_encoding.chat.chatbot import ESGChatbot
from esg_encoding.models import ChatMessage, ChatRequest
from esg_encoding.services import chat_service


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="General ESG answer")
                )
            ]
        )


def _chatbot_with_stale_report_context() -> tuple[ESGChatbot, _FakeCompletions]:
    chatbot = object.__new__(ESGChatbot)
    completions = _FakeCompletions()
    chatbot.config = SimpleNamespace(llm_model="test-model")
    chatbot.llm_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    chatbot.sessions = {}
    chatbot.report_content = SimpleNamespace(document_id="SECRET_REPORT_ID")
    chatbot.compliance_assessment = SimpleNamespace(
        report_id="SECRET_ASSESSMENT_ID",
        total_metrics_analyzed=1,
        overall_compliance_score=0.5,
        disclosure_summary={
            "fully_disclosed": 0,
            "partially_disclosed": 1,
            "not_disclosed": 0,
        },
        metric_analyses=[
            SimpleNamespace(
                metric_name="SECRET_METRIC",
                metric_id="SECRET_METRIC_ID",
                disclosure_status="partially_disclosed",
                reasoning="SECRET_REASONING",
            )
        ],
    )
    chatbot._segment_map = {}
    chatbot._embedding_matrix = None
    chatbot._embedding_segment_ids = []
    chatbot._embedder_model = None
    return chatbot, completions


class ChatContextIsolationTests(unittest.TestCase):
    def test_general_mode_ignores_stale_report_context_and_keeps_its_session(self) -> None:
        chatbot, completions = _chatbot_with_stale_report_context()

        first = chatbot.chat(
            ChatRequest(message="What is ESG?", include_context=False)
        )
        second = chatbot.chat(
            ChatRequest(
                session_id=first.session_id,
                message="Give me an example.",
                include_context=False,
            )
        )

        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(first.relevant_segments, [])
        session = chatbot.sessions[first.session_id]
        self.assertIsNone(session.report_context)
        self.assertIsNone(session.compliance_context)

        system_prompt = completions.calls[-1]["messages"][0]["content"]
        user_prompt = completions.calls[-1]["messages"][1]["content"]
        self.assertIn("general ESG assistant", system_prompt)
        self.assertIn("What is ESG?", user_prompt)
        self.assertIn("without report or compliance-assessment context", user_prompt)
        self.assertNotIn("provided segments", user_prompt)
        self.assertNotIn("[SEGID p#]", user_prompt)
        self.assertNotIn("compliance assessment results", user_prompt)
        for secret in (
            "SECRET_REPORT_ID",
            "SECRET_ASSESSMENT_ID",
            "SECRET_METRIC",
            "SECRET_REASONING",
        ):
            self.assertNotIn(secret, user_prompt)

    def test_report_mode_retains_report_prompt_and_evidence(self) -> None:
        chatbot, completions = _chatbot_with_stale_report_context()

        chatbot._generate_llm_response(
            question="What did the report disclose?",
            question_type="general",
            relevant_content=["SECRET_SEGMENT_CONTENT"],
            conversation_history=[],
            include_report_context=True,
        )

        system_prompt = completions.calls[-1]["messages"][0]["content"]
        user_prompt = completions.calls[-1]["messages"][1]["content"]
        self.assertIn("Use ONLY the provided report segments", system_prompt)
        self.assertIn("SECRET_ASSESSMENT_ID", user_prompt)
        self.assertIn("SECRET_SEGMENT_CONTENT", user_prompt)

    def test_general_mode_does_not_reuse_report_bound_conversation_history(self) -> None:
        chatbot, completions = _chatbot_with_stale_report_context()
        report_session_id = chatbot.create_session("report-session")
        chatbot.sessions[report_session_id].messages.append(
            ChatMessage(
                role="assistant",
                content="SECRET_PRIOR_REPORT_ANSWER",
            )
        )

        response = chatbot.chat(
            ChatRequest(
                session_id=report_session_id,
                message="Now answer generally.",
                include_context=False,
            )
        )

        self.assertNotEqual(response.session_id, report_session_id)
        user_prompt = completions.calls[-1]["messages"][1]["content"]
        self.assertNotIn("SECRET_PRIOR_REPORT_ANSWER", user_prompt)

    def test_contextual_request_tags_a_preexisting_unbound_session(self) -> None:
        chatbot, _ = _chatbot_with_stale_report_context()
        chatbot._search_relevant_content = Mock(return_value=[])
        session_id = chatbot.create_session(
            "initially-general",
            include_report_context=False,
        )

        chatbot.chat(
            ChatRequest(
                session_id=session_id,
                message="Analyze the report.",
                include_context=True,
            )
        )

        session = chatbot.sessions[session_id]
        self.assertEqual(session.report_context, "SECRET_REPORT_ID")
        self.assertEqual(session.compliance_context, "SECRET_ASSESSMENT_ID")

    def test_general_service_skips_latest_report_loading(self) -> None:
        fake_chatbot = SimpleNamespace(chat=Mock(return_value="general-response"))
        request = ChatRequest(message="Explain materiality", include_context=False)

        with (
            patch.dict(chat_service.system_components, {"chatbot": fake_chatbot}),
            patch.object(
                chat_service,
                "_load_latest_assessment_for_chat",
                side_effect=AssertionError("assessment loader must not run"),
            ),
            patch.object(
                chat_service,
                "_load_report_content_for_chat",
                side_effect=AssertionError("report loader must not run"),
            ),
        ):
            result = asyncio.run(chat_service.chat.__wrapped__(request))

        self.assertEqual(result, "general-response")
        fake_chatbot.chat.assert_called_once_with(request)


if __name__ == "__main__":
    unittest.main()
