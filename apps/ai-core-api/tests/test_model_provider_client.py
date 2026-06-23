import pytest

from app.services import model_provider_client
from app.services.model_provider_client import ModelProviderClient


@pytest.mark.asyncio
async def test_openai_compatible_client_uses_bearer_auth_and_chat_completions(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "model": "provider-chat-latest",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(model_provider_client.httpx, "AsyncClient", FakeAsyncClient)

    client = ModelProviderClient(
        base_url="https://provider-one.example/v1",
        deployment_name="provider-chat-latest",
        api_key="test-key",
        request_options={"extra_body": {"thinking": {"type": "disabled"}}},
    )

    result = await client.chat_completion(
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.3,
        max_tokens=2000,
    )

    assert captured["url"] == "https://provider-one.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == "provider-chat-latest"
    assert captured["json"]["thinking"] == {"type": "disabled"}
    assert result["content"] == "ok"
    assert result["total_tokens"] == 3


@pytest.mark.asyncio
async def test_zai_client_does_not_disable_thinking_by_default(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "model": "glm-5.2",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(model_provider_client.httpx, "AsyncClient", FakeAsyncClient)

    client = ModelProviderClient(
        base_url="https://api.z.ai/api/paas/v4",
        deployment_name="glm-5.2",
        api_key="test-key",
    )

    await client.chat_completion(
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.3,
        max_tokens=2000,
    )

    assert captured["url"] == "https://api.z.ai/api/paas/v4/chat/completions"
    assert "thinking" not in captured["json"]


@pytest.mark.asyncio
async def test_client_streams_reasoning_and_content_deltas(monkeypatch):
    captured = {}
    events = []

    lines = [
        'data: {"model":"kimi-k2","choices":[{"delta":{"reasoning_content":"Checking the records. "}}]}',
        'data: {"choices":[{"delta":{"content":"Here is "}}]}',
        'data: {"choices":[{"delta":{"content":"the answer."},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}',
        "data: [DONE]",
    ]

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in lines:
                yield line

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, method, url, headers, json):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeStreamResponse()

    monkeypatch.setattr(model_provider_client.httpx, "AsyncClient", FakeAsyncClient)

    client = ModelProviderClient(
        base_url="https://api.moonshot.ai/v1",
        deployment_name="kimi-k2",
        api_key="test-key",
    )

    result = await client.chat_completion(
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.3,
        max_tokens=2000,
        stream_event_sink=events.append,
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert captured["json"]["stream"] is True
    assert result["content"] == "Here is the answer."
    assert result["reasoning_content"] == "Checking the records. "
    assert result["finish_reason"] == "stop"
    assert result["total_tokens"] == 7
    assert events == [
        {"type": "reasoning_delta", "delta": "Checking the records. "},
        {"type": "content_delta", "delta": "Here is "},
        {"type": "content_delta", "delta": "the answer."},
    ]


@pytest.mark.asyncio
async def test_client_normalizes_cumulative_and_overlapping_stream_chunks(monkeypatch):
    events = []
    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"Finding "}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"Finding products "}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"products with matches. "}}]}',
        'data: {"choices":[{"delta":{"content":"Aqua"}}]}',
        'data: {"choices":[{"delta":{"content":"Aquafresh "}}]}',
        'data: {"choices":[{"delta":{"content":"Aquafresh product table."},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in lines:
                yield line

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return FakeStreamResponse()

    monkeypatch.setattr(model_provider_client.httpx, "AsyncClient", FakeAsyncClient)

    client = ModelProviderClient(
        base_url="https://api.z.ai/api/paas/v4",
        deployment_name="glm-5.2",
        api_key="test-key",
    )

    result = await client.chat_completion(
        messages=[{"role": "user", "content": "hello"}],
        stream_event_sink=events.append,
    )

    assert result["reasoning_content"] == "Finding products with matches. "
    assert result["content"] == "Aquafresh product table."
    assert events == [
        {"type": "reasoning_delta", "delta": "Finding "},
        {"type": "reasoning_delta", "delta": "products "},
        {"type": "reasoning_delta", "delta": "with matches. "},
        {"type": "content_delta", "delta": "Aqua"},
        {"type": "content_delta", "delta": "fresh "},
        {"type": "content_delta", "delta": "product table."},
    ]
