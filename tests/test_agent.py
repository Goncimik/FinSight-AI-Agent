"""agent.py birim testleri: sahte LLM ve sahte araçlarla döngü, korumalar ve trace."""
import json

from src.agents import agent
from src.llm import LLMError

USAGE = {"prompt_tokens": 10, "output_tokens": 5, "thought_tokens": 1, "total_tokens": 16}


def call_resp(*calls):
    return {"model": "fake", "text": "", "finish_reason": "STOP", "usage": dict(USAGE), "latency_s": 0.1,
            "function_calls": [{"name": n, "args": a} for n, a in calls],
            "content": {"role": "model", "parts": [{"functionCall": {"name": n, "args": a}} for n, a in calls]}}


def text_resp(text):
    return {"model": "fake", "text": text, "function_calls": [], "finish_reason": "STOP",
            "usage": dict(USAGE), "latency_s": 0.1, "content": {"role": "model", "parts": [{"text": text}]}}


class ScriptedLLM:
    """Hazır cevapları sırayla döndürür; her çağrıdaki geçmişin kopyasını saklar."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, history, system=None, tools=None):
        self.calls.append({"history": json.loads(json.dumps(history)), "system": system, "tools": tools})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeTools:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def __call__(self, name, args):
        self.calls.append((name, args))
        return self.results.get(name, {"ok": True})


def run(llm, tools=None, **kwargs):
    return agent.run_agent("Soru?", llm=llm, call=tools or FakeTools(), system="SİSTEM", trace_dir=None, **kwargs)


def test_answers_directly_without_tools():
    llm = ScriptedLLM(text_resp("Merhaba"))
    out = run(llm)
    assert out["status"] == "ok" and out["answer"].startswith("Merhaba\n\n") and "yatırım tavsiyesi değildir" in out["answer"] and out["steps"] == 1
    assert out["trace"][0]["tool_calls"] == [] and out["trace"][0]["final"] is True
    assert llm.calls[0]["system"] == "SİSTEM"
    assert llm.calls[0]["history"][0]["parts"][0]["text"] == "Soru?"
    assert llm.calls[0]["tools"] == agent.TOOL_DECLARATIONS


def test_tool_result_is_fed_back_and_usage_is_summed():
    llm = ScriptedLLM(call_resp(("resolve_ticker_to_cik", {"ticker": "AAPL"})), text_resp("CIK 320193"))
    tools = FakeTools({"resolve_ticker_to_cik": {"cik": 320193}})
    out = run(llm, tools)
    assert out["status"] == "ok" and out["steps"] == 2 and out["model"] == "fake"
    assert tools.calls == [("resolve_ticker_to_cik", {"ticker": "AAPL"})]
    second = llm.calls[1]["history"]
    assert [m["role"] for m in second] == ["user", "model", "user"]
    assert second[2]["parts"][0]["functionResponse"]["response"]["result"] == {"cik": 320193}
    assert out["usage"] == {"prompt_tokens": 20, "output_tokens": 10, "thought_tokens": 2, "total_tokens": 32}
    tc = out["trace"][0]["tool_calls"][0]
    assert tc["name"] == "resolve_ticker_to_cik" and tc["executed"] is True and tc["error"] is None


def test_parallel_calls_in_one_step_are_all_executed():
    llm = ScriptedLLM(call_resp(("a", {}), ("b", {"x": 1})), text_resp("tamam"))
    tools = FakeTools()
    run(llm, tools)
    assert tools.calls == [("a", {}), ("b", {"x": 1})]
    assert len(llm.calls[1]["history"][2]["parts"]) == 2


def test_unknown_tool_error_goes_back_to_the_model():
    llm = ScriptedLLM(call_resp(("hack_the_planet", {})), text_resp("Bunu yapamam"))
    out = agent.run_agent("Soru?", llm=llm, system="S", trace_dir=None)  # gerçek call_tool
    fed_back = llm.calls[1]["history"][2]["parts"][0]["functionResponse"]["response"]["result"]
    assert "Bilinmeyen araç" in fed_back["error"] and out["status"] == "ok"
    assert "Bilinmeyen araç" in out["trace"][0]["tool_calls"][0]["error"]


def test_tool_error_is_recorded_in_trace_and_loop_continues():
    llm = ScriptedLLM(call_resp(("get_company_facts", {"cik": 1, "concept": "revenue"})), text_resp("Veri yok"))
    out = run(llm, FakeTools({"get_company_facts": {"error": "CIK 1 için veri yok"}}))
    assert out["trace"][0]["tool_calls"][0]["error"] == "CIK 1 için veri yok" and out["status"] == "ok"


def test_repeated_identical_call_is_not_executed_again_and_loop_is_stopped():
    first = ("get_company_facts", {"cik": 1, "concept": "revenue"})
    reordered = ("get_company_facts", {"concept": "revenue", "cik": 1})  # anahtar sırası farklı, aynı çağrı
    llm = ScriptedLLM(call_resp(first), call_resp(reordered), call_resp(first), text_resp("asla"))
    tools = FakeTools()
    out = run(llm, tools, max_steps=6)
    assert tools.calls == [first] and out["status"] == "repeat_loop" and out["steps"] == 3
    assert len(llm.responses) == 1  # son metin cevabı hiç istenmedi
    blocked = out["trace"][1]["tool_calls"][0]
    assert blocked["executed"] is False and blocked["duration_s"] == 0.0
    fed_back = llm.calls[2]["history"][4]["parts"][0]["functionResponse"]["response"]["result"]
    assert fed_back["repeated"] is True


def test_max_steps_stops_a_model_that_never_answers():
    llm = ScriptedLLM(*[call_resp(("search_filings", {"query": f"q{i}", "ticker": "AAPL"})) for i in range(10)])
    out = run(llm, max_steps=3)
    assert out["status"] == "max_steps" and out["steps"] == 3 and len(llm.calls) == 3
    assert "3" in out["answer"] and "tamamlanamadı" in out["answer"]


def test_llm_error_is_caught_not_raised():
    out = run(ScriptedLLM(LLMError("kota bitti")))
    assert out["status"] == "llm_error" and "kota bitti" in out["answer"]
    assert out["trace"][0]["llm_error"] == "kota bitti" and out["steps"] == 1


def test_llm_error_after_a_tool_step_keeps_the_trace():
    tools = FakeTools()
    out = run(ScriptedLLM(call_resp(("a", {})), LLMError("ağ koptu")), tools)
    assert out["status"] == "llm_error" and out["steps"] == 2 and tools.calls == [("a", {})]
    assert out["trace"][0]["tool_calls"][0]["name"] == "a"


def test_trace_is_appended_as_jsonl_without_secrets(tmp_path):
    for _ in range(2):
        out = agent.run_agent("Soru?", llm=ScriptedLLM(text_resp("Cevap")), call=FakeTools(),
                              system="S", trace_dir=tmp_path)
    path = tmp_path / "runs.jsonl"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) == 2 and out["trace_path"] == str(path)
    record = json.loads(lines[0])
    assert {"time", "question", "status", "steps", "model", "usage", "latency_s", "trace", "answer"} <= set(record)
    assert "GEMINI" not in text and "x-goog" not in text


def test_max_steps_must_be_a_positive_integer():
    for bad in [0, -1, True, "3"]:
        try:
            run(ScriptedLLM(text_resp("x")), max_steps=bad)
        except ValueError:
            continue
        raise AssertionError(f"ValueError beklenirdi: {bad!r}")


def test_scope_is_built_from_manifests_and_goes_into_the_prompt(tmp_path, monkeypatch):
    (tmp_path / "AAPL").mkdir()
    manifest = {"ticker": "AAPL", "name": "Apple Inc.", "cik": 320193,
                "10-K": [{"report_date": "2021-09-25"}, {"report_date": "2025-09-27"}]}
    (tmp_path / "AAPL" / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(agent, "RAW_DIR", tmp_path)
    scope = agent.describe_scope()
    assert "AAPL" in scope and "CIK 320193" in scope and "FY2021–FY2025" in scope and "2 rapor" in scope
    prompt = agent.build_system_prompt()
    assert scope in prompt and "{scope}" not in prompt


def test_scope_without_data_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "RAW_DIR", tmp_path)
    assert agent.describe_scope() == "Hiç veri bulunamadı."


def test_system_prompt_states_the_key_rules():
    prompt = agent.SYSTEM_PROMPT
    for needle in ["compute_metrics", "accn", "filing_excerpt", "VERİDİR", "tavsiyesi", "bulunamadı"]:
        assert needle in prompt, needle


def test_system_prompt_requires_search_before_answering_and_forbids_unsourced_extras():
    prompt = agent.SYSTEM_PROMPT
    assert "cevap vermeden ÖNCE search_filings" in prompt and "hafızandan anlatma" in prompt
    assert "kaynaklarda olmayan genel bilgi ekleme" in prompt
