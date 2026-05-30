"""Tests for money normalization and formatting in the Odoo connector."""
import pytest
from app.core.formatting import (
    _is_money_field,
    _format_money_value,
    normalize_money_values,
    format_search_read_response,
)


class TestMoneyFieldDetection:
    def test_amount_total_is_money(self):
        assert _is_money_field("amount_total") is True

    def test_balance_is_money(self):
        assert _is_money_field("balance") is True

    def test_debit_is_money(self):
        assert _is_money_field("debit") is True

    def test_credit_is_money(self):
        assert _is_money_field("credit") is True

    def test_display_name_not_money(self):
        assert _is_money_field("display_name") is False

    def test_id_not_money(self):
        assert _is_money_field("id") is False

    def test_price_unit_is_money(self):
        assert _is_money_field("price_unit") is True

    def test_amount_currency_is_money(self):
        assert _is_money_field("amount_currency") is True


class TestFormatMoneyValue:
    def test_zar_formatting(self):
        result = _format_money_value(1234.56, "ZAR", "R")
        assert result is not None
        assert result["value"] == 1234.56
        assert result["currency_code"] == "ZAR"
        assert result["currency_symbol"] == "R"
        assert "R" in result["formatted"]
        assert "1 234,56" in result["formatted"]

    def test_usd_formatting(self):
        result = _format_money_value(5000.00, "USD", "$")
        assert result is not None
        assert result["currency_code"] == "USD"
        assert "$" in result["formatted"]

    def test_none_value(self):
        result = _format_money_value(None, "ZAR", "R")
        assert result is None

    def test_string_value(self):
        result = _format_money_value("invalid", "ZAR", "R")
        assert result is None

    def test_integer_value(self):
        result = _format_money_value(1000, "EUR", "€")
        assert result is not None
        assert result["value"] == 1000.0
        assert result["currency_code"] == "EUR"


class TestNormalizeMoneyValues:
    def test_enriches_amount_total(self):
        record = {
            "id": 1,
            "display_name": "Test Invoice",
            "amount_total": 2354.69,
            "currency_id": [1, "ZAR"],
        }
        enriched = normalize_money_values(record)
        assert "amount_total_money" in enriched
        money = enriched["amount_total_money"]
        assert money["currency_code"] == "ZAR"
        assert money["value"] == 2354.69

    def test_skips_non_money_fields(self):
        record = {
            "id": 1,
            "display_name": "Test",
            "state": "posted",
        }
        enriched = normalize_money_values(record)
        assert len(enriched) == len(record)

    def test_multiple_money_fields(self):
        record = {
            "amount_total": 1000.0,
            "amount_residual": 500.0,
            "amount_untaxed": 800.0,
            "currency_id": [1, "ZAR"],
        }
        enriched = normalize_money_values(record)
        assert "amount_total_money" in enriched
        assert "amount_residual_money" in enriched
        assert "amount_untaxed_money" in enriched

    def test_balance_debit_credit(self):
        record = {
            "balance": 1500.0,
            "debit": 1500.0,
            "credit": 0.0,
        }
        enriched = normalize_money_values(record)
        assert "balance_money" in enriched
        assert "debit_money" in enriched
        assert "credit_money" in enriched


class TestFormatSearchReadResponse:
    def test_response_structure(self):
        records = [
            {"id": 1, "display_name": "Invoice 1", "amount_total": 500.0},
            {"id": 2, "display_name": "Invoice 2", "amount_total": 1500.0},
        ]
        result = format_search_read_response(
            model="account.move",
            records=records,
            currency_code="ZAR",
            currency_symbol="R",
        )
        assert result["model"] == "account.move"
        assert result["count"] == 2
        assert len(result["records"]) == 2
        # Money fields should be enriched
        for rec in result["records"]:
            assert "amount_total_money" in rec
            assert rec["amount_total_money"]["currency_code"] == "ZAR"
            assert "__model" in rec

    def test_empty_records(self):
        result = format_search_read_response(
            model="account.move",
            records=[],
            currency_code="ZAR",
        )
        assert result["count"] == 0
        assert result["records"] == []

    def test_currency_passed_via_cache(self):
        records = [
            {"id": 1, "amount_total": 250.0},
        ]
        result = format_search_read_response(
            model="res.currency",
            records=records,
            currency_code="USD",
            currency_symbol="$",
        )
        money = result["records"][0]["amount_total_money"]
        assert money["currency_code"] == "USD"
        assert money["currency_symbol"] == "$"
