"""Central classification of Odoo errors into HTTP responses.

Both the run endpoint (orm_runner._single_call) and the app-level exception
handler use this, so a failing Odoo call is classified the same way whether it is
caught inline or bubbles up:

  * OdooAuthError            -> 401 odoo_auth_failed   (so a caller can tell
                                "your Odoo credentials are wrong" apart from
                                "the call failed", instead of both being a 400)
  * "Invalid field X in leaf" -> 400 invalid_domain_field (friendly message)
  * "cannot delete ... POS"   -> 400 odoo_delete_blocked[_active_pos_session]
  * any other OdooError       -> 400 odoo_call_failed / <ExceptionClassName>

Tracebacks are stripped and long messages truncated before they reach a client.
"""
from __future__ import annotations

import re

from app.core.odoo_client import OdooAuthError, OdooError

MAX_CONNECTOR_ERROR_CHARS = 1200
INVALID_FIELD_RE = re.compile(
    r"Invalid field (?P<model>[\w.]+)\.(?P<field>[\w_]+) in leaf", re.IGNORECASE
)


def _specific_delete_type(lower_message: str) -> str | None:
    if "cannot delete" in lower_message:
        if any(
            marker in lower_message
            for marker in ("pos config", "pos session", "active pos", "point of sale")
        ):
            return "odoo_delete_blocked_active_pos_session"
        return "odoo_delete_blocked"
    return None


def _clean_message(raw_message: str) -> str:
    message = raw_message
    if "Traceback" in message:
        prefix = message.split("Traceback", 1)[0].strip(" ;:\n")
        message = (
            prefix
            if prefix and len(prefix) < 500
            else "Odoo returned an internal error while processing the request."
        )
    if len(message) > MAX_CONNECTOR_ERROR_CHARS:
        message = (
            message[:MAX_CONNECTOR_ERROR_CHARS].rstrip()
            + f"... [truncated {len(raw_message) - MAX_CONNECTOR_ERROR_CHARS} chars]"
        )
    return message


def classify_odoo_error(exc: OdooError) -> tuple[int, dict]:
    """Map an OdooError to (status_code, {error, error_type, message})."""
    if isinstance(exc, OdooAuthError):
        return 401, {
            "error": "odoo_auth_failed",
            "error_type": "OdooAuthError",
            "message": _clean_message(str(exc)),
        }

    raw = str(exc)
    field = INVALID_FIELD_RE.search(raw)
    if field:
        return 400, {
            "error": "invalid_domain_field",
            "error_type": "invalid_domain_field",
            "message": (
                f"Field '{field.group('field')}' does not exist on "
                f"Odoo model '{field.group('model')}'."
            ),
        }

    message = _clean_message(raw)
    specific = _specific_delete_type(message.lower())
    if specific:
        return 400, {"error": specific, "error_type": specific, "message": message}

    return 400, {
        "error": "odoo_call_failed",
        "error_type": type(exc).__name__,
        "message": message,
    }
