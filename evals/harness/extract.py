"""Extract Odoo calls from Workspace tool-call code.

Ground truth (verified against ai-core-api): the model has NO first-class `odoo`
tool. CANONICAL_TOOL_DEFINITIONS exposes only workspace / document_reader. The model reaches Odoo by writing Python that calls
`call('odoo', {...})` INSIDE the `workspace` tool, so a
`message.complete.tool_call_json` entry is `tool_name='workspace'` with the Odoo
operation/method buried in `arguments.code`.

This module parses that code (AST first, regex fallback) to recover the Odoo
calls. It is best-effort by construction:
  * fully-literal call args are recovered exactly;
  * args built from variables / f-strings are flagged `dynamic=True` and only
    partially recovered.
The durable fix is structured per-call broker telemetry (see the effectiveness
"Track B"); until that exists, parsing the code is the most reliable signal.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any

# Odoo ORM methods that only READ. Everything else that is a model method is
# treated as a potential WRITE (default-deny) so mutating workflow/wizard methods
# (action_post, button_draft, *_confirm, resequence, ...) are not missed.
READ_METHODS = frozenset({
    "search", "search_read", "read", "search_count", "read_group",
    "fields_get", "fields_view_get", "name_search", "name_get",
    "default_get", "get_options", "get_report_information",
    "read_progress", "check_access_rights", "exists",
})

WORKSPACE_TOOL_NAMES = frozenset({"workspace"})

_CALL_ODOO_RE = re.compile(r"call\(\s*['\"]odoo['\"]", re.IGNORECASE)


def is_write_method(method: str | None) -> bool:
    """A model method is a potential write unless it is a known read (default-deny)."""
    if not method:
        return False
    return method not in READ_METHODS


@dataclass
class OdooCall:
    operation: str | None = None            # 'playbook' (routing) etc.; None for plain ORM calls
    name: str | None = None                 # playbook name when operation == 'playbook'
    model: str | None = None
    method: str | None = None
    is_batch: bool = False
    sub_methods: list[str] = field(default_factory=list)  # methods inside a batch 'calls' list
    dynamic: bool = False                   # args not fully literal; some fields may be missing
    raw: str = ""

    def methods(self) -> list[str]:
        out: list[str] = []
        if self.method:
            out.append(self.method)
        out.extend(self.sub_methods)
        return out

    def write_methods(self) -> list[str]:
        return [m for m in self.methods() if is_write_method(m)]


# ── AST parsing helpers ───────────────────────────────────────────────────────

def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)[:200]
    except Exception:
        return ""


def _str_keyed(node: ast.Dict) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            out[k.value] = v
    return out


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def _parse_arg_dict(node: ast.AST) -> OdooCall:
    oc = OdooCall(raw=_unparse(node))
    if not isinstance(node, ast.Dict):
        oc.dynamic = True
        return oc
    d = _str_keyed(node)

    def take(key: str, attr: str) -> None:
        if key not in d:
            return
        val = _literal(d[key])
        if isinstance(val, str):
            setattr(oc, attr, val)
        else:
            oc.dynamic = True

    take("operation", "operation")
    take("name", "name")
    take("model", "model")
    take("method", "method")

    if "calls" in d:
        oc.is_batch = True
        calls_node = d["calls"]
        if isinstance(calls_node, ast.List):
            for el in calls_node.elts:
                if isinstance(el, ast.Dict):
                    sub = _str_keyed(el)
                    m = _literal(sub["method"]) if "method" in sub else None
                    if isinstance(m, str):
                        oc.sub_methods.append(m)
                    else:
                        oc.dynamic = True
                else:
                    oc.dynamic = True
        else:
            oc.dynamic = True
    return oc


def _is_call_odoo(node: ast.Call) -> bool:
    func = node.func
    fname = None
    if isinstance(func, ast.Name):
        fname = func.id
    elif isinstance(func, ast.Attribute):
        fname = func.attr
    if fname != "call" or not node.args:
        return False
    first = node.args[0]
    return isinstance(first, ast.Constant) and first.value == "odoo"


def extract_odoo_calls_from_code(code: str) -> list[OdooCall]:
    """Recover every call('odoo', {...}) in a Python snippet."""
    if not code or "odoo" not in code:
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _regex_fallback(code)

    calls: list[OdooCall] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_call_odoo(node):
            if len(node.args) >= 2:
                calls.append(_parse_arg_dict(node.args[1]))
            else:
                calls.append(OdooCall(dynamic=True, raw="call('odoo', <non-literal>)"))
    if not calls and _CALL_ODOO_RE.search(code):
        return _regex_fallback(code)
    return calls


def _regex_fallback(code: str) -> list[OdooCall]:
    """Used when the code doesn't parse; recover what literals we can, flag dynamic."""
    calls: list[OdooCall] = []
    for m in _CALL_ODOO_RE.finditer(code):
        window = code[m.start(): m.start() + 400]
        oc = OdooCall(dynamic=True, raw=window[:120])
        if (op := re.search(r"['\"]operation['\"]\s*:\s*['\"]([\w-]+)['\"]", window)):
            oc.operation = op.group(1)
        if (nm := re.search(r"['\"]name['\"]\s*:\s*['\"]([\w\-./]+)['\"]", window)):
            oc.name = nm.group(1)
        if (meth := re.search(r"['\"]method['\"]\s*:\s*['\"](\w+)['\"]", window)):
            oc.method = meth.group(1)
        calls.append(oc)
    return calls


# ── turn-level extraction ─────────────────────────────────────────────────────

def _as_dict(arguments: Any) -> dict:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            v = json.loads(arguments)
            return v if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def extract_odoo_calls_from_tool_calls(tool_call_json: list | None) -> list[OdooCall]:
    """Pull every Odoo call out of the workspace tool calls of one assistant turn."""
    out: list[OdooCall] = []
    for tc in tool_call_json or []:
        if not isinstance(tc, dict) or tc.get("tool_name") not in WORKSPACE_TOOL_NAMES:
            continue
        args = _as_dict(tc.get("arguments"))
        code = args.get("code")
        if isinstance(code, str):
            out.extend(extract_odoo_calls_from_code(code))
        for f in args.get("files", []) or []:
            if isinstance(f, dict) and isinstance(f.get("content"), str):
                out.extend(extract_odoo_calls_from_code(f["content"]))
    return out
