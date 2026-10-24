"""Gemini REST istemcisi (httpx). Framework kullanmıyoruz: döngü, log ve hata yönetimi bizde.

Neden doğrudan REST? Sıfır ek bağımlılık (Python 3.14 uyumluluk riski yok), her istek ve
yanıt görünür, token ve gecikme doğrudan ölçülür. Anahtar yalnızca istek başlığında gider,
URL'ye ve hata mesajlarına yazılmaz.

Model notları (gemini-3.8-flash): düşünme varsayılan olarak açık (medium); desteklenen
thinking_level değerleri low / medium / high ('minimal' desteklenmez). Gecikmeyi ve token
maliyetini düşürmek için varsayılanımız 'low'. 'temperature' bu modelde kullanımdan kalkmış
sayıldığı için varsayılan olarak GÖNDERİLMEZ; isteğe bağlı parametre olarak duruyor.

Smoke test (iki LLM çağrısı, bir gerçek tool çağrısı):
    python -m src.llm
"""
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.8-flash"  # sabit sürüm; 'latest' takma adı eval'i tekrarlanamaz yapar
DEFAULT_THINKING_LEVEL = "low"
RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """LLM çağrısı başarısız oldu (anahtar yok, kota bitti, zaman aşımı, geçersiz istek...)."""


def _api_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMError("GEMINI_API_KEY bulunamadı. .env dosyasına ekle (bkz. .env.example).")
    return key


def _error_text(resp: "httpx.Response") -> str:
    try:
        message = resp.json()["error"]["message"]
    except Exception:  # noqa: BLE001 - gövde JSON değilse ham metne düş
        message = resp.text[:300]
    hint = " (ücretsiz kota ya da dakika limiti dolmuş olabilir; biraz bekle)" if resp.status_code == 429 else ""
    return f"{message}{hint}"


def _parse(data: dict, model: str, latency: float) -> dict:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = data.get("promptFeedback", {}).get("blockReason", "bilinmiyor")
        raise LLMError(f"Gemini aday yanıt döndürmedi (blockReason: {reason}).")
    content = candidates[0].get("content") or {"role": "model", "parts": []}
    parts = content.get("parts", [])
    usage = data.get("usageMetadata", {})
    return {
        "model": model,
        "text": "".join(p["text"] for p in parts if "text" in p and not p.get("thought")),
        "function_calls": [
            {"name": p["functionCall"]["name"], "args": p["functionCall"].get("args", {})}
            for p in parts if "functionCall" in p
        ],
        "content": content,  # olduğu gibi geçmişe eklenir (thoughtSignature gibi alanlar korunur)
        "finish_reason": candidates[0].get("finishReason"),
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "output_tokens": usage.get("candidatesTokenCount", 0),
            "thought_tokens": usage.get("thoughtsTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
        "latency_s": round(latency, 3),
    }


def tool_results_content(results: list[tuple[str, dict]]) -> dict:
    """[(tool_adı, sonuç_dict), ...] -> modele geri verilecek 'user' içeriği."""
    return {"role": "user",
            "parts": [{"functionResponse": {"name": name, "response": {"result": result}}}
                      for name, result in results]}


def generate(contents: list[dict], system: str | None = None, tools: list[dict] | None = None,
             model: str = DEFAULT_MODEL, thinking_level: str | None = DEFAULT_THINKING_LEVEL,
             temperature: float | None = None, max_retries: int = 2, timeout: float = 90.0,
             client: "httpx.Client | None" = None) -> dict:
    """Gemini'ye bir istek atar.

    Returns:
        {'model', 'text', 'function_calls', 'content', 'finish_reason', 'usage', 'latency_s'}.
        Geçici hatalarda (429/5xx, zaman aşımı, bağlantı kopması) üstel bekleme ile tekrar dener;
        kalıcı hatada LLMError fırlatır (ham httpx hatası dışarı sızmaz).
    """
    config: dict = {}
    if thinking_level:
        config["thinkingConfig"] = {"thinkingLevel": thinking_level}
    if temperature is not None:
        config["temperature"] = temperature
    body: dict = {"contents": contents}
    if config:
        body["generationConfig"] = config
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        body["tools"] = [{"functionDeclarations": tools}]
    headers = {"x-goog-api-key": _api_key()}
    url = f"{BASE_URL}/{model}:generateContent"

    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        for attempt in range(max_retries + 1):
            started = time.monotonic()
            try:
                resp = client.post(url, json=body, headers=headers)
            except httpx.TransportError as exc:  # zaman aşımı, bağlantı kopması
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"Gemini'ye ulaşılamadı ({type(exc).__name__}). "
                               "Ağı kontrol et ya da biraz sonra tekrar dene.") from exc
            latency = time.monotonic() - started
            if resp.status_code == 200:
                return _parse(resp.json(), model, latency)
            if resp.status_code in RETRY_STATUS and attempt < max_retries:
                time.sleep(2 ** attempt)  # 1, 2 sn
                continue
            raise LLMError(f"Gemini HTTP {resp.status_code}: {_error_text(resp)}")
    finally:
        if owns_client:
            client.close()
    raise LLMError("Beklenmeyen durum: istek döngüsü sonuçsuz bitti.")  # pragma: no cover


if __name__ == "__main__":
    from src.tools.resolve_ticker import resolve_ticker_to_cik

    declarations = [{
        "name": "resolve_ticker_to_cik",
        "description": "Borsa kodunun (ticker) SEC CIK numarasını ve şirket adını bulur.",
        "parameters": {"type": "OBJECT",
                       "properties": {"ticker": {"type": "STRING", "description": "Borsa kodu, ör. AAPL"}},
                       "required": ["ticker"]},
    }]
    system_prompt = "Kısa ve Türkçe cevap ver. Gerektiğinde araçları kullan."
    history = [{"role": "user", "parts": [{"text": "Apple'ın (ticker AAPL) CIK numarası nedir?"}]}]

    print(f"model: {DEFAULT_MODEL} | thinking_level: {DEFAULT_THINKING_LEVEL} | istek gönderiliyor...", flush=True)
    first = generate(history, system=system_prompt, tools=declarations)
    print("1. çağrı - araç çağrısı:", first["function_calls"])
    print("          token:", first["usage"], f"| {first['latency_s']} sn")
    if first["function_calls"]:
        history.append(first["content"])
        results = [(c["name"], resolve_ticker_to_cik(**c["args"])) for c in first["function_calls"]]
        history.append(tool_results_content(results))
        second = generate(history, system=system_prompt, tools=declarations)
        print("2. çağrı - cevap:", second["text"])
        print("          token:", second["usage"], f"| {second['latency_s']} sn")
