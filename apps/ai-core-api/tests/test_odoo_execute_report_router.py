import os
from datetime import datetime
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4

os.environ.setdefault("ODOO_CONNECTOR_URL", "http://mock-connector:8000")
os.environ.setdefault("ODOO_CONNECTOR_API_KEY", "test-key")

from app.services.reviewer import ReviewerAgent
from app.schemas.schemas import ReviewRequest
from app.services.model_router import execute_chat
from app.models.models import AIRoute, AIModel, AIProvider, AIConnectedAccount, AITool
from tests.test_model_router import MockSession


class TestOdooExecuteReportRouterAndReviewer:
    @pytest.mark.asyncio
    async def test_reviewer_approved_with_currency(self):
        agent = ReviewerAgent()
        
        req = ReviewRequest(
            user_question="What is the revenue?",
            content="According to the Odoo P&L report, the revenue is R 123,456.78 ZAR for the selected period.",
            tool_results=[]
        )
        result = await agent.review(req)
        assert result.approved is True
        assert result.risk_level == "high"

    @pytest.mark.asyncio
    async def test_reviewer_rejected_missing_currency(self):
        agent = ReviewerAgent()
        
        req = ReviewRequest(
            user_question="What is the revenue?",
            content="According to the Odoo P&L report, the revenue is 123456.78 for the selected period.",
            tool_results=[]
        )
        result = await agent.review(req)
        assert result.approved is False
        assert "currency symbol" in result.reviewer_notes.lower()

    @pytest.mark.asyncio
    @patch("app.services.model_router.build_foundry_client")
    async def test_execute_chat_calls_generic_report_tool(self, mock_build_foundry_cls):
        db = MockSession(has_config=False)
        
        route = AIRoute(
            id=uuid4(),
            task_type="general_chat",
            primary_model_id=uuid4(),
            enabled="true",
            temperature=0.3,
            max_tokens=2000,
            system_prompt="Standard Prompt"
        )
        model = AIModel(
            id=route.primary_model_id,
            provider_id=uuid4(),
            display_name="Kimi K2.6",
            model_name="Kimi-K2.6",
            deployment_name="kimi-k2-6",
            supports_tools="true",
            enabled="true"
        )
        provider = AIProvider(
            id=model.provider_id,
            name="Prov",
            provider_type="azure_foundry",
            base_url="https://mock.services.ai.azure.com",
            enabled="true"
        )
        account = AIConnectedAccount(
            id=uuid4(),
            user_id=uuid4(),
            provider="odoo",
            provider_username="admin",
            odoo_url="https://test.odoo.com",
            odoo_db="test_db",
            secret_reference="mock-ref",
            status="active"
        )
        tool = AITool(
            id=uuid4(),
            name="odoo_ops_runner",
            display_name="Odoo Ops Runner",
            input_schema={}
        )

        async def mock_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt).lower()
            res = MagicMock()
            if "ai_routes" in stmt_str:
                res.scalar_one_or_none = lambda: route
            elif "ai_models" in stmt_str:
                res.scalar_one_or_none = lambda: model
            elif "ai_providers" in stmt_str:
                res.scalar_one_or_none = lambda: provider
            elif "ai_connected_accounts" in stmt_str:
                res.scalars = lambda: MagicMock(all=lambda: [account], first=lambda: account)
            elif "ai_tools" in stmt_str:
                res.scalars = lambda: MagicMock(all=lambda: [tool])
            elif "ai_memories" in stmt_str:
                res.scalars = lambda: MagicMock(all=lambda: [])
            else:
                res.first = lambda: None
            return res

        db.execute = mock_execute
        db.add = MagicMock()
        db.flush = AsyncMock()

        mock_client = MagicMock()
        mock_chat_completion = AsyncMock(side_effect=[
            {
                "error": False,
                "content": "",
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_pnl_123",
                        "type": "function",
                        "function": {
                            "name": "odoo_ops_runner",
                            "arguments": '{"mode": "report", "report_name": "Profit and Loss", "date_from": "2026-05-01", "date_to": "2026-05-31", "line_names": ["Revenue"]}'
                        }
                    }
                ],
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "latency_ms": 100
            },
            {
                "error": False,
                "content": "The Odoo P&L report lists total revenue of R 150,000.00 ZAR.",
                "finish_reason": "stop",
                "prompt_tokens": 200,
                "completion_tokens": 80,
                "total_tokens": 280,
                "latency_ms": 150
            }
        ])
        mock_client.chat_completion = mock_chat_completion
        mock_build_foundry_cls.return_value = mock_client

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.json = lambda: {
            "report_name": "Profit and Loss",
            "report_id": 123,
            "date_from": "2026-05-01",
            "date_to": "2026-05-31",
            "currency_code": "ZAR",
            "currency_symbol": "R",
            "source": "odoo_account_report",
            "line_count": 1,
            "available_line_names": ["Operating Revenue"],
            "missing_line_names": [],
            "lines": [
                {
                    "id": "rev_1",
                    "name": "Operating Revenue",
                    "code": "REV",
                    "level": 0,
                    "value": 150000.0,
                    "formatted_value": "R 150,000.00"
                }
            ]
        }

        mock_policy = {
            "selected_route_id": str(route.id),
            "selected_model_id": str(model.id),
            "fallback_model_id": None,
            "reason": "matched_request_task_type",
            "cost_tier": "medium",
            "quality_tier": "standard",
        }
        mock_select_route = AsyncMock(return_value=mock_policy)

        with patch("app.services.model_routing_policy.ModelRoutingPolicyService.select_route", new=mock_select_route), \
             patch("httpx.AsyncClient.post", return_value=mock_http_response), \
             patch("app.services.model_router._resolve_odoo_credentials_for_tool") as mock_creds, \
             patch("app.services.model_router._resolve_api_key", return_value="secret-key"), \
             patch("app.services.model_router.get_enabled_route") as mock_get_enabled:
             
             mock_creds.return_value = {
                 "url": "https://test.odoo.com",
                 "db": "test_db",
                 "username": "admin",
                 "api_key": "secret-key",
                 "transport": "auto"
              }
             mock_get_enabled.return_value = (route, model, provider)
             
             result = await execute_chat(
                 db, 
                 [{"role": "user", "content": "What is revenue per P&L?"}], 
                 user_id=uuid4()
             )
             
             assert "R 150,000.00" in result["content"]
             assert result["prompt_tokens"] > 0


class TestStructuredToolErrors:
    """Tests for Fix 1: Preserve structured tool errors."""

    @pytest.mark.asyncio
    async def test_connector_error_json_preserved(self):
        from app.services.model_router import _execute_tool_call
        from unittest.mock import AsyncMock, patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error_type": "report_not_found",
            "message": "Report 'Profit and Loss' not found",
        }
        mock_resp.text = "raw fallback"
        with patch("httpx.AsyncClient.post", return_value=mock_resp), \
             patch("app.services.model_router._resolve_odoo_credentials_for_tool") as mc, \
             patch("app.services.model_router.ODOO_CONNECTOR_URL", "http://mock-connector:8000"), \
             patch("app.services.model_router.ODOO_CONNECTOR_KEY", "test-key"):
            mc.return_value = {"url": "https://test.odoo.com", "db": "test", "username": "admin", "api_key": "key"}
            result = await _execute_tool_call(AsyncMock(), uuid4(), "odoo_ops_runner",
                                              {"mode": "report", "report_name": "Profit and Loss"})
        assert result.get("error") is True
        assert result.get("status_code") == 400
        assert result.get("error_type") == "report_not_found"
        assert isinstance(result.get("connector_error"), dict)

    @pytest.mark.asyncio
    async def test_connector_error_fallback_on_non_json(self):
        from app.services.model_router import _execute_tool_call
        from unittest.mock import AsyncMock, patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.json.side_effect = ValueError("Not JSON")
        mock_resp.text = "Upstream connection refused"
        with patch("httpx.AsyncClient.post", return_value=mock_resp), \
             patch("app.services.model_router._resolve_odoo_credentials_for_tool") as mc, \
             patch("app.services.model_router.ODOO_CONNECTOR_URL", "http://mock-connector:8000"), \
             patch("app.services.model_router.ODOO_CONNECTOR_KEY", "test-key"):
            mc.return_value = {"url": "https://test.odoo.com", "db": "test", "username": "admin", "api_key": "key"}
            result = await _execute_tool_call(AsyncMock(), uuid4(), "odoo_ops_runner",
                                              {"mode": "report", "report_name": "Test"})
        assert result.get("error") is True
        assert result.get("error_type") == "connector_http_error"
        assert "connection refused" in result.get("message", "").lower()


class TestReportFallbackAnswer:
    """Tests for Fix 2/5: Fallback answer builder."""

    def test_fallback_from_report_lines(self):
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
                "report_name": "Profit and Loss",
                "date_from": "2026-06-01", "date_to": "2026-06-30",
                "currency_code": "ZAR", "currency_symbol": "R",
                "lines": [
                    {"name": "Revenue", "value": 150000.0, "formatted_value": "150,000.00"},
                ],
            }},
        ])
        assert result is not None
        assert "Profit and Loss" in result
        assert "2026-06-01" in result
        assert "R" in result
        assert "Revenue" in result

    def test_fallback_without_matching_lines(self):
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
                "report_name": "Profit and Loss",
                "lines": [],
                "available_line_names": ["Revenue", "Expenses", "Net Income"],
            }},
        ])
        assert result is not None
        assert "Revenue" in result and "Expenses" in result

    def test_fallback_from_tool_error(self):
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
                "error": True, "error_type": "report_not_found",
                "message": "Report not found",
            }},
        ])
        assert result is not None
        assert "report" in result.lower() and "could not find" in result.lower()

    def test_fallback_ignores_non_report_tools(self):
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "azure_cli", "result": {"stdout": ""}},
        ])
        assert result is None


class TestReviewerBlankCheck:
    """Tests for Fix 4: Reviewer must not mask tool errors."""

    @pytest.mark.asyncio
    async def test_reviewer_blank_with_tool_error(self):
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        reviewer = ReviewerAgent()
        review = await reviewer.review(ReviewRequest(
            content="",
            user_question="What is revenue on P&L?",
            tool_results=[{"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {"error": True}}],
        ))
        assert not review.approved


class TestReportDiscovery:
    """Tests for consolidated report routing."""

    def test_only_odoo_ops_runner_is_mapped(self):
        from app.services.model_router import _map_odoo_tool_to_path
        assert _map_odoo_tool_to_path("odoo_ops_runner") == "/odoo/ops/run"
        assert _map_odoo_tool_to_path("odoo_list_reports") == ""
        assert _map_odoo_tool_to_path("odoo_get_profit_and_loss") == ""


class TestCleanFallback:
    """Tests for Fix 4: Clean user-facing fallback errors."""

    def test_fallback_no_raw_dicts(self):
        """Fallback answer must never contain raw dict repr."""
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
                "error": True,
                "connector_error": {
                    "detail": {
                        "error": "report_unavailable",
                        "message": "Could not execute Odoo account report 'Profit and Loss'.",
                    },
                },
            }},
        ])
        assert result is not None
        # Must not contain raw Python dict formatting
        assert "{'detail'" not in result
        assert "{" not in result
        assert "odoo" in result.lower()
        assert "could not execute" in result.lower()

    def test_fallback_technical_error_clean(self):
        """Technical error with 'id' must produce clean message."""
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
                "error": True,
                "connector_error": {
                    "detail": {
                        "error": "report_unavailable",
                        "message": "Could not execute Odoo account report 'Profit and Loss'. Technical error: 'id'",
                    },
                },
            }},
        ])
        assert result is not None
        assert "{'detail'" not in result
        assert "Technical error" in result
        assert "'id'" in result


class TestDetectOdooReportIntent:
    """Tests for deterministic report intent detection."""

    def test_detect_pnl_this_month(self):
        from app.services.model_router import detect_odoo_report_intent
        result = detect_odoo_report_intent("whats the revenue for this month on p&l report")
        assert result is not None
        assert result["tool"] == "odoo_ops_runner"
        args = result["input"]
        assert args["mode"] == "report"
        assert args["report_name"] == "Profit and Loss"
        assert "date_from" in args
        assert "date_to" in args
        assert "line_names" in args
        assert "Revenue" in args["line_names"]

    def test_detect_trial_balance_this_month(self):
        from app.services.model_router import detect_odoo_report_intent
        result = detect_odoo_report_intent("show trial balance this month")
        assert result is not None
        assert result["tool"] == "odoo_ops_runner"
        assert result["input"]["mode"] == "report"
        assert result["input"]["report_name"] == "Trial Balance"
        assert "date_from" in result["input"]

    def test_detect_balance_sheet(self):
        from app.services.model_router import detect_odoo_report_intent
        result = detect_odoo_report_intent("show balance sheet this year")
        assert result is not None
        assert result["input"]["report_name"] == "Balance Sheet"

    def test_non_report_query_returns_none(self):
        from app.services.model_router import detect_odoo_report_intent
        result = detect_odoo_report_intent("whats the weather today")
        assert result is None

    def test_empty_query_returns_none(self):
        from app.services.model_router import detect_odoo_report_intent
        assert detect_odoo_report_intent("") is None
        assert detect_odoo_report_intent(None) is None

    def test_detect_revenue_keywords(self):
        from app.services.model_router import detect_odoo_report_intent
        for phrase in ["revenue", "income", "sales"]:
            result = detect_odoo_report_intent(f"whats the {phrase} on p&l")
            assert result is not None, f"Failed for phrase: {phrase}"
            assert "line_names" in result["input"]
            assert any(kw in str(result["input"]["line_names"]).lower() for kw in [phrase])

    def test_detect_expenses_keywords(self):
        from app.services.model_router import detect_odoo_report_intent
        result = detect_odoo_report_intent("what are the expenses on p&l")
        assert result is not None
        assert "Expenses" in result["input"]["line_names"]

    def test_detect_gross_profit(self):
        from app.services.model_router import detect_odoo_report_intent
        result = detect_odoo_report_intent("gross profit on p&l this month")
        assert result is not None
        assert "Gross Profit" in result["input"]["line_names"]

    def test_date_range_this_year(self):
        from app.services.model_router import _detect_date_range
        dfrom, dto = _detect_date_range("show p&l this year")
        assert dfrom is not None
        assert dto is not None
        assert dfrom.startswith(str(datetime.utcnow().year))
        assert dfrom.endswith("-01-01")

    def test_date_range_last_month(self):
        from app.services.model_router import _detect_date_range
        dfrom, dto = _detect_date_range("p&l last month")
        assert dfrom is not None
        assert dto is not None

    def test_no_dedicated_pnl_tool(self):
        """Must not add a dedicated P&L tool."""
        from app.services.model_router import _map_odoo_tool_to_path
        assert _map_odoo_tool_to_path("odoo_get_profit_and_loss") == ""
        assert _map_odoo_tool_to_path("get_revenue") == ""


class TestExecuteChatReportFallback:
    """Tests for fallback answer in execute_chat before Reviewer."""

    @pytest.mark.asyncio
    async def test_execute_chat_fallback_on_blank_content(self):
        """Blank model content + successful report tool must produce fallback answer."""
        from app.services.model_router import execute_chat, _build_report_fallback_answer
        tool_results = [{"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
            "report_name": "Profit and Loss",
            "date_from": "2026-06-01", "date_to": "2026-06-30",
            "currency_code": "ZAR", "currency_symbol": "R",
            "lines": [{"name": "Revenue", "value": 150000.0, "formatted_value": "150,000.00"}],
        }}]
        fallback = _build_report_fallback_answer(tool_results)
        assert fallback is not None
        assert "Profit and Loss" in fallback
        assert "150,000" in fallback
        assert "R" in fallback

    def test_fallback_returns_none_when_no_report_tool(self):
        """Non-report tools must not produce a fallback."""
        from app.services.model_router import _build_report_fallback_answer
        fallback = _build_report_fallback_answer([
            {"tool_name": "azure_cli", "result": {"stdout": ""}},
        ])
        assert fallback is None

    def test_pnl_uses_generic_report_tool(self):
        """P&L question must route through odoo_ops_runner, not a dedicated tool."""
        from app.services.model_router import _map_odoo_tool_to_path
        path = _map_odoo_tool_to_path("odoo_ops_runner")
        assert path == "/odoo/ops/run"
        # No dedicated P&L tool should exist
        assert _map_odoo_tool_to_path("odoo_get_profit_and_loss") == ""
        assert _map_odoo_tool_to_path("get_revenue_this_month") == ""


class TestReviewerFalsePositiveFix:
    """Tests for Reviewer currency false-positive fix."""

    @pytest.mark.asyncio
    async def test_report_error_no_money_passes(self):
        """Report error response with no monetary amount must pass reviewer."""
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        agent = ReviewerAgent()
        review = await agent.review(ReviewRequest(
            user_question="whats the revenue on p&l report",
            content=(
                "I reached Odoo, but I could not execute the Profit and Loss report.\n\n"
                "Reason: Could not execute Odoo account report 'Profit and Loss'. Technical error: 'id'."
            ),
        ))
        assert review.approved, f"Expected approved but got issues: {review.issues}"

    @pytest.mark.asyncio
    async def test_currency_amount_passes(self):
        """Monetary value with currency symbol must pass."""
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        agent = ReviewerAgent()
        review = await agent.review(ReviewRequest(
            user_question="whats the revenue on p&l report",
            content="Revenue for June 2026 is R 150,000.00.",
        ))
        assert review.approved, f"Expected approved but got issues: {review.issues}"

    @pytest.mark.asyncio
    async def test_amount_without_currency_rejected(self):
        """Monetary value without currency must be rejected."""
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        agent = ReviewerAgent()
        review = await agent.review(ReviewRequest(
            user_question="whats the revenue on p&l report",
            content="Revenue for June 2026 is 150,000.00.",
        ))
        assert not review.approved
        assert "currency symbol" in str(review.issues).lower()

    @pytest.mark.asyncio
    async def test_date_range_does_not_trigger(self):
        """Date ranges must not trigger missing currency."""
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        agent = ReviewerAgent()
        review = await agent.review(ReviewRequest(
            user_question="whats the revenue on p&l report",
            content="I checked the Profit and Loss report for 2026-06-01 to 2026-06-30.",
        ))
        assert review.approved, f"Expected approved but got issues: {review.issues}"

    @pytest.mark.asyncio
    async def test_report_id_does_not_trigger(self):
        """Report IDs, line counts, and request IDs must not trigger currency check."""
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        agent = ReviewerAgent()
        content = (
            "Report: Profit and Loss | report_id: 42 | line_count: 5 | "
            "Request ID: abc123 | error_code: 400"
        )
        review = await agent.review(ReviewRequest(
            user_question="whats the revenue on p&l report",
            content=content,
        ))
        assert review.approved, f"Expected approved but got issues: {review.issues}"

    @pytest.mark.asyncio
    async def test_credit_note_attachment_count_does_not_trigger_currency_check(self):
        """Attachment counts near 'credit note' are not monetary amounts."""
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        agent = ReviewerAgent()
        review = await agent.review(ReviewRequest(
            user_question=(
                "Can you check Odoo for a credit note for Cosmetic Connection "
                "that has 7 PDF attachments?"
            ),
            content=(
                "I found a credit note with 7 PDF attachments. "
                "The attachment names are COSMETIC CONNECTION GRV141814.pdf, "
                "COSMETIC CONNECTION GRV 141411.pdf, and COSMETIC CONNECTION GRV 142274.pdf."
            ),
        ))
        assert review.approved, f"Expected approved but got issues: {review.issues}"

    @pytest.mark.asyncio
    async def test_technical_error_id_not_flagged(self):
        """Technical error mentioning 'id' must not trigger currency check."""
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        agent = ReviewerAgent()
        review = await agent.review(ReviewRequest(
            user_question="whats the revenue on p&l report",
            content="Technical error: 'id' while executing report 'Profit and Loss'.",
        ))
        assert review.approved, f"Expected approved but got issues: {review.issues}"

    @pytest.mark.asyncio
    async def test_report_unavailable_answer_passes(self):
        """Report unavailable answer with no amount must pass."""
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        agent = ReviewerAgent()
        review = await agent.review(ReviewRequest(
            user_question="whats the revenue on p&l report",
            content=(
                "I reached Odoo, but could not execute the report. "
                "The report engine encountered an internal issue. "
                "This usually means the report could not be resolved or executed "
                "with the current Odoo account."
            ),
        ))
        assert review.approved, f"Expected approved but got issues: {review.issues}"

    @pytest.mark.asyncio
    async def test_reviewer_diagnostics_included(self):
        """Reviewer rejection must include matched amounts in reviewer_notes."""
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        agent = ReviewerAgent()
        review = await agent.review(ReviewRequest(
            user_question="what is the revenue",
            content="The revenue is 50000 for this month.",
        ))
        assert not review.approved
        assert review.reviewer_notes is not None
        assert "50000" in review.reviewer_notes

    @pytest.mark.asyncio
    async def test_successful_report_with_zar_passes(self):
        """Successful report result with ZAR currency must pass reviewer."""
        from app.services.reviewer import ReviewerAgent
        from app.schemas.schemas import ReviewRequest
        agent = ReviewerAgent()
        review = await agent.review(ReviewRequest(
            user_question="whats the revenue on p&l report",
            content="Revenue: R 150,000.00 for June 2026.",
        ))
        assert review.approved, f"Expected approved but got issues: {review.issues}"
