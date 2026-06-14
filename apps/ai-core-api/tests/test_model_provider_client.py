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
                "model": "kimi-k2.6",
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
        base_url="https://api.moonshot.ai/v1",
        deployment_name="kimi-k2.6",
        api_key="test-key",
        request_options={"extra_body": {"thinking": {"type": "disabled"}}},
    )

    result = await client.chat_completion(
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.3,
        max_tokens=2000,
    )

    assert captured["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == "kimi-k2.6"
    assert captured["json"]["thinking"] == {"type": "disabled"}
    assert result["content"] == "ok"
    assert result["total_tokens"] == 3
