"""Workspace execution for model-generated analysis code.

This is the platform "cloud workspace" surface: scripts run in a temporary
working directory with a clean environment, captured output, and brokered
connector/tool helpers. Connector secrets are never written into the workspace.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
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

WORKSPACE_TOOL_NAME = "workspace"
WORKSPACE_BACKEND = "local-workspace"
MAX_CODE_CHARS = int(os.environ.get("WORKSPACE_MAX_CODE_CHARS", "60000"))
MAX_INPUT_FILES = int(os.environ.get("WORKSPACE_MAX_INPUT_FILES", "10"))
MAX_INPUT_FILE_CHARS = int(os.environ.get("WORKSPACE_MAX_INPUT_FILE_CHARS", "100000"))
MAX_TIMEOUT_SECONDS = int(os.environ.get("WORKSPACE_MAX_TIMEOUT_SECONDS", "120"))
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("WORKSPACE_DEFAULT_TIMEOUT_SECONDS", "60"))
MAX_OUTPUT_CHARS = int(os.environ.get("WORKSPACE_MAX_OUTPUT_CHARS", "20000"))
MAX_COLLECTED_FILES = int(os.environ.get("WORKSPACE_MAX_COLLECTED_FILES", "20"))
MAX_COLLECTED_FILE_BYTES = int(os.environ.get("WORKSPACE_MAX_COLLECTED_FILE_BYTES", str(15 * 1024 * 1024)))
MAX_FILE_PREVIEW_CHARS = int(os.environ.get("WORKSPACE_MAX_FILE_PREVIEW_CHARS", "4000"))
CHILD_MEMORY_MB = int(os.environ.get("WORKSPACE_CHILD_MEMORY_MB", "512"))

PYTHON_LANGUAGES = {"python", "py"}
SHELL_LANGUAGES = {"shell", "sh", "bash", "terminal"}
SUPPORTED_LANGUAGES = PYTHON_LANGUAGES | SHELL_LANGUAGES
INTERNAL_WORKSPACE_FILES = {
    "main.py",
    "main.sh",
    "__ai_platform_runner.py",
    "ai_platform_tools.py",
    "ai-platform-tool",
}

PYTHON_RUNNER = """
from ai_platform_tools import PlatformToolError, call, call_checked, call_raw

namespace = {
    "__name__": "__main__",
    "__file__": "main.py",
    "PlatformToolError": PlatformToolError,
    "call": call,
    "call_checked": call_checked,
    "call_raw": call_raw,
}
with open("main.py", "r", encoding="utf-8") as handle:
    source = handle.read()
exec(compile(source, "main.py", "exec"), namespace)
"""


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
        self.error_counts: dict[str, int] = {}
        self.error_details: list[dict[str, Any]] = []
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
            self.error_counts[tool_name] = self.error_counts.get(tool_name, 0) + 1
            self.error_details.append(_connector_error_detail(tool_name, arguments, result))
            return {
                "ok": False,
                "error": True,
                "error_type": str(result.get("error_type") or "workspace_tool_error"),
                "message": str(result.get("message") or result.get("error") or f"{tool_name} failed."),
                "result": result,
            }
        return {"ok": True, "result": result}


def _connector_error_detail(tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "tool_name": tool_name,
        "error_type": str(result.get("error_type") or result.get("error") or "connector_error"),
        "message": _truncate_text(str(result.get("message") or result.get("error") or "Connector call failed."), 1200),
    }
    for key in ("operation", "model", "method"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            detail[key] = value
    if "calls" in arguments and isinstance(arguments["calls"], list):
        detail["batch_call_count"] = len(arguments["calls"])
    return detail


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


def tool_error(response):
    """Return a compact error object from a failed broker response."""
    return {{
        "error": True,
        "error_type": response.get("error_type") or "platform_tool_error",
        "message": response.get("message") or "Platform tool call failed",
        "result": response.get("result"),
    }}


def call(tool_name, arguments=None, raise_on_error=False):
    """Call a platform tool/connector and return its result or an error object."""
    response = call_raw(tool_name, arguments)
    if response.get("error"):
        if not raise_on_error:
            return tool_error(response)
        raise PlatformToolError(response.get("message") or response.get("error_type") or "Platform tool call failed", response)
    return response.get("result")


def call_checked(tool_name, arguments=None):
    """Call a platform tool/connector and raise PlatformToolError on failure."""
    return call(tool_name, arguments, raise_on_error=True)
'''
    (workdir / "ai_platform_tools.py").write_text(textwrap.dedent(tools_helper).strip() + "\n", encoding="utf-8")

    cli = '''#!/usr/bin/env python3
import json
import sys

from ai_platform_tools import call_raw


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
        arguments = json.loads(raw) if raw else {}
    except Exception as exc:
        print(f"Invalid JSON arguments: {exc}", file=sys.stderr)
        return 2
    try:
        response = call_raw(tool_name, arguments)
    except Exception as exc:
        print(json.dumps({"error": True, "message": str(exc), "error_type": type(exc).__name__}, ensure_ascii=False, default=str), file=sys.stderr)
        return 1
    if response.get("error"):
        print(json.dumps(response, ensure_ascii=False, default=str), file=sys.stderr)
        return 1
    print(json.dumps(response.get("result"), ensure_ascii=False, default=str))
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
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        item: dict[str, Any] = {
            "path": str(rel_path),
            "bytes": size,
            "mime_type": mime_type,
            "sha256": _sha256_file(path) if size <= MAX_COLLECTED_FILE_BYTES else None,
        }
        if size <= MAX_COLLECTED_FILE_BYTES:
            data = path.read_bytes()
            item["content_base64"] = base64.b64encode(data).decode("ascii")
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
    runner = workdir / "__ai_platform_runner.py"
    script.write_text(code, encoding="utf-8")
    runner.write_text(textwrap.dedent(PYTHON_RUNNER).strip() + "\n", encoding="utf-8")
    preexec_fn = (lambda: _limit_child_process(timeout_seconds)) if os.name == "posix" else None
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(runner),
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


async def run_workspace(
    arguments: dict[str, Any],
    *,
    tool_executor: WorkspaceToolExecutor | None = None,
) -> dict[str, Any]:
    async with WorkspaceSession(tool_executor=tool_executor) as session:
        return await session.run(arguments)


class WorkspaceSession:
    """Persistent workspace directory and broker for one chat tool loop."""

    def __init__(
        self,
        *,
        tool_executor: WorkspaceToolExecutor | None = None,
        workspace_id: str | None = None,
    ) -> None:
        self.tool_executor = tool_executor
        self.workspace_id = workspace_id or uuid.uuid4().hex
        self.workdir = _workspace_root() / self.workspace_id
        self._broker: WorkspaceToolBroker | None = None
        self._entered = False
        self._run_index = 0

    async def __aenter__(self) -> "WorkspaceSession":
        self.workdir.mkdir(parents=True, exist_ok=False)
        broker = WorkspaceToolBroker(self.tool_executor, self.workdir)
        self._broker = await broker.__aenter__()
        _write_tool_helpers(self.workdir, self._broker)
        self._entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            if self._broker:
                await self._broker.__aexit__(*exc)
        finally:
            self._entered = False
            if os.environ.get("WORKSPACE_KEEP_RUN_DIRS", "").lower() not in {"1", "true", "yes"}:
                shutil.rmtree(self.workdir, ignore_errors=True)

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._entered or self._broker is None:
            return {
                "status": "failed",
                "error": True,
                "error_type": "workspace_session_not_started",
                "message": "Workspace session has not been started.",
            }

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

        self._run_index += 1
        before_calls = self._broker.calls
        before_call_counts = dict(self._broker.call_counts)
        before_error_counts = dict(self._broker.error_counts)
        before_error_detail_count = len(self._broker.error_details)

        try:
            input_files = _write_input_files(self.workdir, arguments.get("files"))
            if language in PYTHON_LANGUAGES:
                exit_code, stdout, stderr, timed_out = await _run_python(self.workdir, code, timeout_seconds)
            else:
                exit_code, stdout, stderr, timed_out = await _run_shell(self.workdir, code, timeout_seconds, language)
            files = _collect_files(self.workdir)
            status = "failed" if timed_out or exit_code not in (0, None) else "success"
            connector_calls = {
                tool_name: count - before_call_counts.get(tool_name, 0)
                for tool_name, count in self._broker.call_counts.items()
                if count - before_call_counts.get(tool_name, 0) > 0
            }
            connector_error_calls = {
                tool_name: count - before_error_counts.get(tool_name, 0)
                for tool_name, count in self._broker.error_counts.items()
                if count - before_error_counts.get(tool_name, 0) > 0
            }
            connector_error_details = self._broker.error_details[before_error_detail_count:]
            response = {
                "status": status,
                "backend": WORKSPACE_BACKEND,
                "workspace_id": self.workspace_id,
                "run_index": self._run_index,
                "language": language,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "timeout_seconds": timeout_seconds,
                "stdout": _truncate_text(stdout),
                "stderr": _truncate_text(stderr),
                "files": files,
                "input_files": input_files,
                "tool_calls": self._broker.calls - before_calls,
                "connector_calls": connector_calls,
                "connector_error_calls": connector_error_calls,
                "connector_error_details": connector_error_details,
                "odoo_calls": connector_calls.get("odoo", 0),
                "workspace_tool_calls_total": self._broker.calls,
                "connector_calls_total": dict(self._broker.call_counts),
                "connector_error_calls_total": dict(self._broker.error_counts),
                "connector_error_details_total": self._broker.error_details[-20:],
                "helper_modules": ["ai_platform_tools"],
                "helper_commands": ["ai-platform-tool"],
                "error": bool(status == "failed"),
                "message": "Workspace execution failed." if status == "failed" else "Workspace execution completed.",
            }
            return response
        except ValueError as exc:
            return {"status": "failed", "error": True, "error_type": "invalid_workspace_arguments", "message": str(exc)}
