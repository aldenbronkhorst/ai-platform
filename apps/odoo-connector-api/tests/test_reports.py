import pytest
import os
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Enable debug mode for tests
os.environ["DEBUG"] = "true"

from app.main import app

client = TestClient(app)


class TestProfitAndLossReport:
    @patch("app.routers.reports.OdooClient")
    def test_generic_execute_pnl_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        # Mock search finding a report id, then get_report_informations
        mock_client.call_with_transport.side_effect = [
            [123],  # report search
            {
                "lines": [
                    {
                        "name": "Operating Revenue",
                        "columns": [{"no_format_name": 150000.0, "name": "R 150,000.00"}]
                    },
                    {
                        "name": "Cost of Goods Sold",
                        "columns": [{"no_format_name": -50000.0, "name": "-R 50,000.00"}]
                    }
                ]
            }
        ]
        
        response = client.post("/reports/execute", json={
            "credentials": {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret"
            },
            "report_name": "P&L",  # Alias!
            "date_from": "2026-05-01",
            "date_to": "2026-05-31",
            "line_names": ["Revenue"]  # Filter!
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["report_name"] == "Profit and Loss"
        assert data["currency_symbol"] == "TES"  # From test_db[:3] prefix "TES" -> "TES"
        assert data["line_count"] == 1
        assert data["lines"][0]["name"] == "Operating Revenue"
        assert "Cost of Goods Sold" in data["available_line_names"]

    @patch("app.routers.reports.OdooClient")
    def test_generic_execute_trial_balance_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        mock_client.call_with_transport.side_effect = [
            [456],  # report search
            {
                "lines": [
                    {
                        "name": "Cash",
                        "columns": [{"no_format_name": 10000.0, "name": "$10,000.00"}]
                    }
                ]
            }
        ]
        
        response = client.post("/reports/execute", json={
            "credentials": {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret"
            },
            "report_name": "Trial Balance",
            "line_names": ["Cash", "MissingLine"]  # Filter with a missing line name
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["report_name"] == "Trial Balance"
        assert "MissingLine" in data["missing_line_names"]
        assert data["line_count"] == 1

    @patch("app.routers.reports.OdooClient")
    def test_pnl_fallback_to_invoices(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        # account.report fails (raises Exception)
        mock_client.call_with_transport.side_effect = Exception("Report model not found")
        
        # search_read on account.move succeeds and returns moves
        mock_client.search_read.return_value = [
            {"id": 1, "amount_untaxed": 45000.0, "currency_id": [2, "USD"]},
            {"id": 2, "amount_untaxed": 55000.0, "currency_id": [2, "USD"]}
        ]
        
        response = client.post("/reports/execute", json={
            "credentials": {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret"
            },
            "report_name": "Profit & Loss",
            "date_from": "2026-05-01",
            "date_to": "2026-05-31"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "Fallback" in data["report_name"]
        assert data["source"] == "fallback_posted_customer_invoices"
        assert data["lines"][0]["value"] == 100000.0

    @patch("app.routers.reports.OdooClient")
    def test_unsupported_report_unavailable_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        # Balance Sheet fails
        mock_client.call_with_transport.side_effect = Exception("Model not installed")
        
        response = client.post("/reports/execute", json={
            "credentials": {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret"
            },
            "report_name": "Balance Sheet"
        })
        
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["error"] == "report_unavailable"
        assert data["detail"]["attempted_report_name"] == "Balance Sheet"
        assert "supported" in data["detail"]["likely_causes"][3].lower()
