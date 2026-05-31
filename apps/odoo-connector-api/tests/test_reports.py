import pytest
import os
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Enable debug mode for tests
os.environ["DEBUG"] = "true"

from app.main import app

client = TestClient(app)


class TestOdooReportExecution:
    @patch("app.routers.reports.OdooClient")
    def test_generic_execute_pnl_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        # Mock search_read resolving the report_id
        mock_client.search_read.side_effect = [
            [{"id": 123, "name": "Profit and Loss"}]
        ]
        
        # Mock get_options then get_report_information
        mock_client.call_with_transport.side_effect = [
            {"options": {}},  # get_options
            {  # get_report_information
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
        assert data["currency_symbol"] is None
        assert data["currency_source"] == "unknown"
        assert data["line_count"] == 1
        assert data["lines"][0]["name"] == "Operating Revenue"
        assert "Cost of Goods Sold" in data["available_line_names"]

    @patch("app.routers.reports.OdooClient")
    def test_generic_execute_trial_balance_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        # Mock search_read resolving the report_id
        mock_client.search_read.side_effect = [
            [{"id": 456, "name": "Trial Balance"}]
        ]
        
        # Mock get_options then get_report_information
        mock_client.call_with_transport.side_effect = [
            {"options": {}},  # get_options
            {  # get_report_information
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
    def test_report_execution_failure_returns_unavailable(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        # P&L report execution fails — no hidden fallback
        mock_client.search_read.side_effect = Exception("No report model")
        
        response = client.post("/reports/execute", json={
            "credentials": {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret"
            },
            "report_name": "Profit and Loss",
            "date_from": "2026-05-01",
            "date_to": "2026-05-31"
        })
        
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["error"] == "report_unavailable"
        assert "Profit and Loss" in data["detail"]["attempted_report_name"]

    @patch("app.routers.reports.OdooClient")
    def test_unsupported_report_unavailable_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        # Balance Sheet search_read fails
        mock_client.search_read.side_effect = Exception("Model not installed")
        
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
