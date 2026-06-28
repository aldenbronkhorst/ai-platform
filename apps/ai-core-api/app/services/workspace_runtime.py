"""Workspace execution for model-generated analysis code.

This is the platform "cloud workspace" surface: scripts run in a temporary
working directory with a clean environment, captured output, and brokered
connector/tool helpers. Connector secrets are never written into the workspace.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import textwrap
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

try:
    import resource
except Exception:  # pragma: no cover - non-Unix fallback
    resource = None  # type: ignore[assignment]


WorkspaceToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
WorkspaceOdooExecutor = Callable[[str, str, list[Any], dict[str, Any]], Awaitable[dict[str, Any]]]

WORKSPACE_TOOL_NAME = "workspace"
WORKSPACE_BACKEND = "local-workspace"
MAX_CODE_CHARS = int(os.environ.get("WORKSPACE_MAX_CODE_CHARS", "60000"))
MAX_INPUT_FILES = int(os.environ.get("WORKSPACE_MAX_INPUT_FILES", "10"))
MAX_INPUT_FILE_CHARS = int(os.environ.get("WORKSPACE_MAX_INPUT_FILE_CHARS", "100000"))
MAX_TIMEOUT_SECONDS = int(os.environ.get("WORKSPACE_MAX_TIMEOUT_SECONDS", "120"))
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("WORKSPACE_DEFAULT_TIMEOUT_SECONDS", "60"))
MAX_OUTPUT_CHARS = int(os.environ.get("WORKSPACE_MAX_OUTPUT_CHARS", "20000"))
MAX_COLLECTED_FILES = int(os.environ.get("WORKSPACE_MAX_COLLECTED_FILES", "20"))
MAX_COLLECTED_FILE_BYTES = int(os.environ.get("WORKSPACE_MAX_COLLECTED_FILE_BYTES", "1000000"))
MAX_FILE_PREVIEW_CHARS = int(os.environ.get("WORKSPACE_MAX_FILE_PREVIEW_CHARS", "4000"))
CHILD_MEMORY_MB = int(os.environ.get("WORKSPACE_CHILD_MEMORY_MB", "512"))

PYTHON_LANGUAGES = {"python", "py"}
SHELL_LANGUAGES = {"shell", "sh", "bash", "terminal"}
SUPPORTED_LANGUAGES = PYTHON_LANGUAGES | SHELL_LANGUAGES
INTERNAL_WORKSPACE_FILES = {
    "main.py",
    "main.sh",
    "ai_platform_odoo.py",
    "ai_platform_tools.py",
    "ai-platform-tool",
}


def _truncate_text(value: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + f"\n[truncated {len(value) - limit} characters]"


def _safe_relative_path(raw_path: str) -> Path:
    path = Path(str(raw_path or "").strip())
    if not str(path):
        raise ValueError("File path is required.")
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe workspace path: {raw_path}")
    if any(part in {"", ".", os.curdir, os.pardir} for part in path.parts):
        raise ValueError(f"Unsafe workspace path: {raw_path}")
    return path


def _workspace_root() -> Path:
    configured = os.environ.get("WORKSPACE_RUNTIME_ROOT")
    root = Path(configured) if configured else Path(tempfile.gettempdir()) / "ai-platform-workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _clean_env(workdir: Path) -> dict[str, str]:
    bin_dir = workdir / "bin"
    return {
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "TEMP": str(workdir),
        "TMP": str(workdir),
        "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(workdir),
        "AI_PLATFORM_WORKSPACE": "1",
    }


def _limit_child_process(timeout_seconds: int) -> None:
    if resource is None:
        return
    cpu_limit = max(1, timeout_seconds + 2)
    memory_bytes = max(128, CHILD_MEMORY_MB) * 1024 * 1024
    file_bytes = max(1, MAX_COLLECTED_FILE_BYTES * MAX_COLLECTED_FILES)
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
    except Exception:
        pass
    for limit_name, value in (
        ("RLIMIT_AS", memory_bytes),
        ("RLIMIT_DATA", memory_bytes),
        ("RLIMIT_FSIZE", file_bytes),
        ("RLIMIT_NOFILE", 128),
    ):
        limit = getattr(resource, limit_name, None)
        if limit is None:
            continue
        try:
            resource.setrlimit(limit, (value, value))
        except Exception:
            pass


def _validate_timeout(value: Any) -> int:
    try:
        timeout = int(value or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(timeout, MAX_TIMEOUT_SECONDS))


def _validate_code(arguments: dict[str, Any], language: str) -> str:
    code = str(arguments.get("code") or "")
    if not code.strip():
        raise ValueError(f"Workspace requires non-empty {language} code.")
    if len(code) > MAX_CODE_CHARS:
        raise ValueError(f"Workspace code is too large; max {MAX_CODE_CHARS} characters.")
    return code


def _write_input_files(workdir: Path, raw_files: Any) -> list[dict[str, Any]]:
    if raw_files in (None, ""):
        return []
    if not isinstance(raw_files, list):
        raise ValueError("files must be a list of {path, content} objects.")
    if len(raw_files) > MAX_INPUT_FILES:
        raise ValueError(f"Too many workspace input files; max {MAX_INPUT_FILES}.")

    written: list[dict[str, Any]] = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise ValueError("Each workspace input file must be an object.")
        rel_path = _safe_relative_path(str(item.get("path") or ""))
        content = str(item.get("content") or "")
        if len(content) > MAX_INPUT_FILE_CHARS:
            raise ValueError(f"Workspace input file {rel_path} is too large.")
        target = workdir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append({"path": str(rel_path), "bytes": len(content.encode("utf-8"))})
    return written


class WorkspaceToolBroker:
    def __init__(self, executor: WorkspaceToolExecutor | None, workdir: Path) -> None:
        self.executor = executor
        self.token = secrets.token_urlsafe(32)
        self.host = "127.0.0.1"
        self.port = 0
        socket_root = Path(os.environ.get("WORKSPACE_SOCKET_ROOT") or "/tmp")
        socket_root.mkdir(parents=True, exist_ok=True)
        socket_name = f"aip-{hashlib.sha256(str(workdir).encode('utf-8')).hexdigest()[:16]}.sock"
        self.socket_path = str(socket_root / socket_name) if os.name == "posix" else ""
        self.calls = 0
        self.call_counts: dict[str, int] = {}
        self._server: asyncio.AbstractServer | None = None

    async def __aenter__(self) -> "WorkspaceToolBroker":
        if self.socket_path:
            self._server = await asyncio.start_unix_server(self._handle, path=self.socket_path)
        else:
            self._server = await asyncio.start_server(self._handle, self.host, 0)
            sockets = self._server.sockets or []
            if not sockets:
                raise RuntimeError("Workspace tool broker did not start.")
            self.port = int(sockets[0].getsockname()[1])
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self.socket_path:
            try:
                Path(self.socket_path).unlink(missing_ok=True)
            except Exception:
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=30)
            request = json.loads(raw.decode("utf-8"))
            response = await self._execute_request(request)
        except Exception as exc:
            response = {"error": True, "message": str(exc), "error_type": type(exc).__name__}
        try:
            writer.write(json.dumps(response, ensure_ascii=False, default=str).encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _execute_request(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or request.get("token") != self.token:
            return {"error": True, "error_type": "workspace_broker_auth_failed", "message": "Invalid workspace broker token."}
        if self.executor is None:
            return {"error": True, "error_type": "workspace_tools_not_available", "message": "Platform tools are not available in this workspace."}

        tool_name = str(request.get("tool_name") or "").strip()
        arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
        if not tool_name:
            return {"error": True, "error_type": "invalid_workspace_tool_call", "message": "Tool name is required."}
        self.calls += 1
        self.call_counts[tool_name] = self.call_counts.get(tool_name, 0) + 1
        result = await self.executor(tool_name, arguments)
        if isinstance(result, dict) and (result.get("error") or result.get("status") == "failed"):
            return {
                "ok": False,
                "error": True,
                "error_type": str(result.get("error_type") or "workspace_tool_error"),
                "message": str(result.get("message") or result.get("error") or f"{tool_name} failed."),
                "result": result,
            }
        return {"ok": True, "result": result}


def _write_tool_helpers(workdir: Path, broker: WorkspaceToolBroker) -> None:
    tools_helper = f'''
import json
import socket

_HOST = {broker.host!r}
_PORT = {broker.port!r}
_SOCKET_PATH = {broker.socket_path!r}
_TOKEN = {broker.token!r}


class PlatformToolError(RuntimeError):
    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload or {{}}


def call_raw(tool_name, arguments=None):
    """Call any platform tool/connector through the Workspace broker."""
    payload = {{
        "token": _TOKEN,
        "tool_name": tool_name,
        "arguments": arguments or {{}},
    }}
    if _SOCKET_PATH:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(60)
        sock.connect(_SOCKET_PATH)
    else:
        sock = socket.create_connection((_HOST, _PORT), timeout=60)
    with sock:
        sock.sendall(json.dumps(payload, default=str).encode("utf-8") + b"\\n")
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


def call(tool_name, arguments=None):
    """Call a platform tool/connector and return its tool result."""
    response = call_raw(tool_name, arguments)
    if response.get("error"):
        raise PlatformToolError(response.get("message") or response.get("error_type") or "Platform tool call failed", response)
    return response.get("result")
'''
    (workdir / "ai_platform_tools.py").write_text(textwrap.dedent(tools_helper).strip() + "\n", encoding="utf-8")

    odoo_helper = '''
from ai_platform_tools import PlatformToolError, call


class OdooWorkspaceError(PlatformToolError):
    pass


def execute_kw(model, method, args=None, kwargs=None):
    """Call the connected user's Odoo account through the platform broker."""
    tool_result = call("odoo", {
        "model": model,
        "method": method,
        "args": args or [],
        "kwargs": kwargs or {},
    })
    if isinstance(tool_result, dict) and tool_result.get("error"):
        raise OdooWorkspaceError(tool_result.get("message") or tool_result.get("error_type") or "Odoo call failed", tool_result)
    if isinstance(tool_result, dict) and "result" in tool_result:
        return tool_result["result"]
    return tool_result


def search(model, domain, **kwargs):
    return execute_kw(model, "search", [domain], kwargs)


def read(model, ids, fields=None, **kwargs):
    options = dict(kwargs)
    if fields is not None:
        options["fields"] = fields
    return execute_kw(model, "read", [ids], options)


def search_read(model, domain, fields=None, **kwargs):
    options = dict(kwargs)
    if fields is not None:
        options["fields"] = fields
    return execute_kw(model, "search_read", [domain], options)


def search_count(model, domain, **kwargs):
    return execute_kw(model, "search_count", [domain], kwargs)
'''
    (workdir / "ai_platform_odoo.py").write_text(textwrap.dedent(odoo_helper).strip() + "\n", encoding="utf-8")

    cli = f'''#!{sys.executable}
import json
import sys

from ai_platform_tools import PlatformToolError, call


def _usage():
    print("Usage: ai-platform-tool <tool_name> [json_arguments]", file=sys.stderr)
    print("       echo JSON | ai-platform-tool <tool_name>", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        _usage()
        return 2
    tool_name = sys.argv[1]
    raw = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
    raw = raw.strip()
    try:
        arguments = json.loads(raw) if raw else {{}}
    except Exception as exc:
        print(f"Invalid JSON arguments: {{exc}}", file=sys.stderr)
        return 2
    try:
        result = call(tool_name, arguments)
    except PlatformToolError as exc:
        print(json.dumps(exc.payload or {{"error": True, "message": str(exc)}}, ensure_ascii=False, default=str), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    bin_dir = workdir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    cli_path = bin_dir / "ai-platform-tool"
    cli_path.write_text(textwrap.dedent(cli).strip() + "\n", encoding="utf-8")
    cli_path.chmod(0o700)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_text_preview(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _collect_files(workdir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    root = workdir.resolve()
    for path in sorted(workdir.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if not path.is_file() or path.name in INTERNAL_WORKSPACE_FILES:
            continue
        if len(files) >= MAX_COLLECTED_FILES:
            files.append({"truncated": True, "message": "Additional workspace files were omitted from the result."})
            break
        if path.is_symlink():
            continue
        resolved = path.resolve()
        try:
            rel_path = resolved.relative_to(root)
        except ValueError:
            continue
        size = path.stat().st_size
        item: dict[str, Any] = {
            "path": str(rel_path),
            "bytes": size,
            "sha256": _sha256_file(path) if size <= MAX_COLLECTED_FILE_BYTES else None,
        }
        if size <= MAX_FILE_PREVIEW_CHARS:
            data = path.read_bytes()
            if _is_text_preview(data):
                item["preview"] = data.decode("utf-8")
        elif size > MAX_COLLECTED_FILE_BYTES:
            item["omitted_content"] = True
            item["message"] = "File is too large to include in the workspace result."
        files.append(item)
    return files


async def _run_python(workdir: Path, code: str, timeout_seconds: int) -> tuple[int | None, str, str, bool]:
    script = workdir / "main.py"
    script.write_text(code, encoding="utf-8")
    preexec_fn = (lambda: _limit_child_process(timeout_seconds)) if os.name == "posix" else None
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        cwd=str(workdir),
        env=_clean_env(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=preexec_fn,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        return process.returncode, stdout_bytes.decode("utf-8", errors="replace"), stderr_bytes.decode("utf-8", errors="replace"), False
    except asyncio.TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        return None, stdout_bytes.decode("utf-8", errors="replace"), stderr_bytes.decode("utf-8", errors="replace"), True


def _shell_executable(language: str) -> str:
    if language == "bash":
        return shutil.which("bash") or "/bin/bash"
    return shutil.which("sh") or "/bin/sh"


async def _run_shell(workdir: Path, code: str, timeout_seconds: int, language: str) -> tuple[int | None, str, str, bool]:
    script = workdir / "main.sh"
    script.write_text(code, encoding="utf-8")
    script.chmod(0o700)
    preexec_fn = (lambda: _limit_child_process(timeout_seconds)) if os.name == "posix" else None
    process = await asyncio.create_subprocess_exec(
        _shell_executable(language),
        str(script),
        cwd=str(workdir),
        env=_clean_env(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=preexec_fn,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        return process.returncode, stdout_bytes.decode("utf-8", errors="replace"), stderr_bytes.decode("utf-8", errors="replace"), False
    except asyncio.TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        return None, stdout_bytes.decode("utf-8", errors="replace"), stderr_bytes.decode("utf-8", errors="replace"), True


def _normalize_language(value: Any) -> str:
    language = str(value or "python").strip().lower()
    if language in {"py"}:
        return "python"
    if language in {"terminal"}:
        return "shell"
    return language


def _tool_executor_from_odoo(odoo_executor: WorkspaceOdooExecutor | None) -> WorkspaceToolExecutor | None:
    if odoo_executor is None:
        return None

    async def execute(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "odoo":
            return {
                "status": "failed",
                "error": True,
                "error_type": "workspace_tool_not_available",
                "message": f"{tool_name} is not available in this workspace.",
            }
        model = str(arguments.get("model") or "")
        method = str(arguments.get("method") or "")
        args = arguments.get("args") if isinstance(arguments.get("args"), list) else []
        kwargs = arguments.get("kwargs") if isinstance(arguments.get("kwargs"), dict) else {}
        return await odoo_executor(model, method, args, kwargs)

    return execute


async def run_workspace(
    arguments: dict[str, Any],
    *,
    tool_executor: WorkspaceToolExecutor | None = None,
    odoo_executor: WorkspaceOdooExecutor | None = None,
) -> dict[str, Any]:
    language = _normalize_language(arguments.get("language"))
    if language not in SUPPORTED_LANGUAGES:
        return {
            "status": "failed",
            "error": True,
            "error_type": "unsupported_workspace_language",
            "message": "Workspace supports language='python', 'shell', 'bash', 'sh', or 'terminal'.",
        }

    try:
        code = _validate_code(arguments, language)
        timeout_seconds = _validate_timeout(arguments.get("timeout"))
    except ValueError as exc:
        return {"status": "failed", "error": True, "error_type": "invalid_workspace_arguments", "message": str(exc)}

    workspace_id = uuid.uuid4().hex
    workdir = _workspace_root() / workspace_id
    workdir.mkdir(parents=True, exist_ok=False)

    try:
        input_files = _write_input_files(workdir, arguments.get("files"))
        executor = tool_executor or _tool_executor_from_odoo(odoo_executor)
        async with WorkspaceToolBroker(executor, workdir) as broker:
            _write_tool_helpers(workdir, broker)
            if language in PYTHON_LANGUAGES:
                exit_code, stdout, stderr, timed_out = await _run_python(workdir, code, timeout_seconds)
            else:
                exit_code, stdout, stderr, timed_out = await _run_shell(workdir, code, timeout_seconds, language)
            files = _collect_files(workdir)
            status = "failed" if timed_out or exit_code not in (0, None) else "success"
            if timed_out:
                status = "failed"
            return {
                "status": status,
                "backend": WORKSPACE_BACKEND,
                "workspace_id": workspace_id,
                "language": language,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "timeout_seconds": timeout_seconds,
                "stdout": _truncate_text(stdout),
                "stderr": _truncate_text(stderr),
                "files": files,
                "input_files": input_files,
                "tool_calls": broker.calls,
                "connector_calls": dict(broker.call_counts),
                "odoo_calls": broker.call_counts.get("odoo", 0),
                "helper_modules": ["ai_platform_tools", "ai_platform_odoo"],
                "helper_commands": ["ai-platform-tool"],
                "error": bool(status == "failed"),
                "message": "Workspace execution failed." if status == "failed" else "Workspace execution completed.",
            }
    except ValueError as exc:
        return {"status": "failed", "error": True, "error_type": "invalid_workspace_arguments", "message": str(exc)}
    finally:
        if os.environ.get("WORKSPACE_KEEP_RUN_DIRS", "").lower() not in {"1", "true", "yes"}:
            shutil.rmtree(workdir, ignore_errors=True)
