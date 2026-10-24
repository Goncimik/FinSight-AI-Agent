"""llm.py birim testleri. Ağa çıkılmaz: httpx.MockTransport ile sahte Gemini kullanılır."""
import json

import httpx

from src import llm


def prepare(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "load_dotenv", lambda *a, **k: None)  # gerçek .env okunmasın
    sleeps: list[float] = []
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)
    return sleeps


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def ok(parts, usage=None):
    body = {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
            "usageMetadata": usage or {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15}}
    return httpx.Response(200, json=body)


USER = [{"role": "user", "parts": [{"text": "merhaba"}]}]


def test_parses_text_usage_and_latency(monkeypatch):
    prepare(monkeypatch)
    out = llm.generate(USER, client=mock_client(lambda req: ok([{"text": "Selam "}, {"text": "dünya"}])))
    assert out["text"] == "Selam dünya" and out["function_calls"] == []
    assert out["usage"] == {"prompt_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert out["model"] == llm.DEFAULT_MODEL and out["latency_s"] >= 0


def test_parses_function_calls_and_keeps_raw_content(monkeypatch):
    prepare(monkeypatch)
    parts = [{"functionCall": {"name": "resolve_ticker_to_cik", "args": {"ticker": "AAPL"}},
              "thoughtSignature": "abc123"}]
    out = llm.generate(USER, client=mock_client(lambda req: ok(parts)))
    assert out["function_calls"] == [{"name": "resolve_ticker_to_cik", "args": {"ticker": "AAPL"}}]
    assert out["content"]["parts"][0]["thoughtSignature"] == "abc123"  # geçmişe olduğu gibi eklenebilir


def test_thought_parts_are_not_part_of_the_answer_text(monkeypatch):
    prepare(monkeypatch)
    out = llm.generate(USER, client=mock_client(
        lambda req: ok([{"text": "iç düşünce", "thought": True}, {"text": "cevap"}])))
    assert out["text"] == "cevap"


def test_request_has_tools_system_temperature_and_key_only_in_header(monkeypatch):
    prepare(monkeypatch)
    seen = {}

    def handler(req):
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers.get("x-goog-api-key"), json.loads(req.content)
        return ok([{"text": "tamam"}])

    tools = [{"name": "t", "description": "d", "parameters": {"type": "OBJECT", "properties": {}}}]
    llm.generate(USER, system="sistem", tools=tools, temperature=0.0, client=mock_client(handler))
    assert seen["key"] == "test-key" and "test-key" not in seen["url"]
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "sistem"
    assert seen["body"]["tools"] == [{"functionDeclarations": tools}]
    assert seen["body"]["generationConfig"]["temperature"] == 0.0
    assert seen["url"].endswith(f"{llm.DEFAULT_MODEL}:generateContent")


def test_retries_on_429_then_succeeds_with_backoff(monkeypatch):
    sleeps = prepare(monkeypatch)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"message": "kota"}}) if calls["n"] < 3 else ok([{"text": "geldi"}])

    out = llm.generate(USER, client=mock_client(handler))
    assert out["text"] == "geldi" and calls["n"] == 3 and sleeps == [1, 2]


def test_raises_after_retries_are_exhausted_with_quota_hint(monkeypatch):
    prepare(monkeypatch)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"message": "Resource exhausted"}})

    try:
        llm.generate(USER, max_retries=2, client=mock_client(handler))
    except llm.LLMError as exc:
        assert "429" in str(exc) and "kota" in str(exc) and calls["n"] == 3
    else:
        raise AssertionError("LLMError beklenirdi")


def test_http_400_is_not_retried_and_shows_the_api_message(monkeypatch):
    sleeps = prepare(monkeypatch)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "Invalid argument"}})

    try:
        llm.generate(USER, client=mock_client(handler))
    except llm.LLMError as exc:
        assert "400" in str(exc) and "Invalid argument" in str(exc)
    else:
        raise AssertionError("LLMError beklenirdi")
    assert calls["n"] == 1 and sleeps == []


def test_missing_api_key_raises_clear_error(monkeypatch):
    prepare(monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY")
    try:
        llm.generate(USER, client=mock_client(lambda req: ok([{"text": "x"}])))
    except llm.LLMError as exc:
        assert "GEMINI_API_KEY" in str(exc)
    else:
        raise AssertionError("LLMError beklenirdi")


def test_blocked_prompt_raises_with_reason(monkeypatch):
    prepare(monkeypatch)
    blocked = httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    try:
        llm.generate(USER, client=mock_client(lambda req: blocked))
    except llm.LLMError as exc:
        assert "SAFETY" in str(exc)
    else:
        raise AssertionError("LLMError beklenirdi")


def test_tool_results_content_shape():
    content = llm.tool_results_content([("a", {"x": 1}), ("b", {"error": "yok"})])
    assert content["role"] == "user"
    assert content["parts"][0] == {"functionResponse": {"name": "a", "response": {"result": {"x": 1}}}}
    assert content["parts"][1]["functionResponse"]["response"]["result"] == {"error": "yok"}
