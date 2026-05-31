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
    def test_pnl_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        # Mock search finding a report id, then get_report_informations
        mock_client.call_with_transport.side_effect = [
            [123],  # report search
            {
                "lines": [
                    {
                        "name": "Total Revenue",
                        "columns": [{"no_format_name": 150000.0}]
                    }
                ]
            }
        ]
        
        response = client.post("/reports/profit-and-loss", json={
            "credentials": {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret"
            },
            "date_from": "2026-05-01",
            "date_to": "2026-05-31",
            "currency": "ZAR"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["report"] == "Profit and Loss"
        assert data["currency_code"] == "ZAR"
        assert data["currency_symbol"] == "R"
        assert data["revenue"]["value"] == 150000.0
        assert data["revenue"]["source"] == "odoo_account_report"

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
        
        response = client.post("/reports/profit-and-loss", json={
            "credentials": {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret"
            },
            "date_from": "2026-05-01",
            "date_to": "2026-05-31"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "Fallback" in data["report"]
        assert data["revenue"]["value"] == 100000.0
        assert data["revenue"]["source"] == "fallback_posted_customer_invoices"
        assert "warning" in data["revenue"]

    @patch("app.routers.reports.OdooClient")
    def test_pnl_unsupported_failure(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        # Both report and fallback fail
        mock_client.call_with_transport.side_effect = Exception("Report error")
        mock_client.search_read.side_effect = Exception("Search error")
        
        response = client.post("/reports/profit-and-loss", json={
            "credentials": {
                "url": "https://test.odoo.com",
                "db": "test_db",
                "username": "admin",
                "api_key": "secret"
            }
        })
        
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert data["detail"]["error"] == "report_unsupported"
        assert "fallback" in data["detail"]["message"].lower()
