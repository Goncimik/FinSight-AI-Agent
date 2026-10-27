"""Gemini REST istemcisi (httpx). Framework kullanmıyoruz: döngü, log ve hata yönetimi bizde.

Neden doğrudan REST? Sıfır ek bağımlılık (Python 3.14 uyumluluk riski yok), her istek ve
yanıt görünür, token ve gecikme doğrudan ölçülür. Anahtar yalnızca istek başlığında gider,
URL'ye ve hata mesajlarına yazılmaz.

Kota yönetimi (ücretsiz katman): 429 geldiğinde sunucunun bildirdiği bekleme süresi (retryDelay) kadar
beklenip yeniden denenir (dakikalık limit). Günlük kota dolduysa (quotaId 'PerDay' ya da çok uzun bekleme)
boşuna denenmez, QuotaExhausted fırlatılır. LLM_MIN_INTERVAL ortam değişkeni ardışık istekler arasına
en az o kadar saniye koyar (ör. LLM_MIN_INTERVAL=6 -> dakikada en fazla 10 istek).

Model notları (gemini-3.x flash ailesi): düşünme varsayılan olarak açık (medium); desteklenen
thinking_level değerleri low / medium / high ('minimal' desteklenmez). Gecikmeyi ve token
maliyetini düşürmek için varsayılanımız 'low'. 'temperature' bu modelde kullanımdan kalkmış
sayıldığı için varsayılan olarak GÖNDERİLMEZ; isteğe bağlı parametre olarak duruyor.

Smoke test (iki LLM çağrısı, bir gerçek tool çağrısı):
    python -m src.llm
"""
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.5-flash"  # sabit sürüm; 'latest' takma adı eval'i tekrarlanamaz yapar
DEFAULT_THINKING_LEVEL = "low"
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_QUOTA_WAIT = 120.0  # bundan uzun bekleme isteyen 429 günlük kota sayılır
MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL", "0") or 0)
_last_call = 0.0


class LLMError(RuntimeError):
    """LLM çağrısı başarısız oldu (anahtar yok, kota bitti, zaman aşımı, geçersiz istek...)."""


class QuotaExhausted(LLMError):
    """Günlük kota doldu; beklemek yeniden denemekten daha anlamsız, kota sıfırlanana kadar durulmalı."""


def _api_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMError("GEMINI_API_KEY bulunamadı. .env dosyasına ekle (bkz. .env.example).")
    return key


def _throttle() -> None:
    """Ardışık istekler arasına MIN_INTERVAL saniye koyar (dakikalık istek limitini aşmamak için)."""
    global _last_call
    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _quota_info(resp: "httpx.Response") -> tuple[str, float | None, list[str]]:
    """429 gövdesinden (mesaj, bekleme_sn ya da None, kota_kimlikleri) çıkarır."""
    try:
        err = resp.json()["error"]
    except Exception:  # noqa: BLE001 - gövde JSON değilse ham metne düş
        return resp.text[:300], None, []
    message = str(err.get("message", ""))
    delay, ids = None, []
    for detail in err.get("details") or []:
        kind = str(detail.get("@type", ""))
        if kind.endswith("RetryInfo"):
            found = re.search(r"[\d.]+", str(detail.get("retryDelay", "")))
            if found:
                delay = float(found.group())
        elif kind.endswith("QuotaFailure"):
            ids += [v.get("quotaId") or v.get("quotaMetric") or "" for v in detail.get("violations", [])]
    if delay is None:
        found = re.search(r"retry in ([\d.]+)\s*s", message, re.IGNORECASE)
        if found:
            delay = float(found.group(1))
    return message, delay, ids


def _is_daily(delay: float | None, ids: list[str]) -> bool:
    return any("perday" in i.lower().replace("_", "") for i in ids) or (delay is not None and delay > MAX_QUOTA_WAIT)


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
             temperature: float | None = None, max_retries: int = 3, timeout: float = 180.0,
             client: "httpx.Client | None" = None) -> dict:
    """Gemini'ye bir istek atar.

    Returns:
        {'model', 'text', 'function_calls', 'content', 'finish_reason', 'usage', 'latency_s'}.
        Geçici hatalarda (429/5xx, zaman aşımı, bağlantı kopması) bekleyip tekrar dener; kalıcı hatada
        LLMError, günlük kota dolduysa QuotaExhausted fırlatır (ham httpx hatası dışarı sızmaz).
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
            _throttle()
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
            if resp.status_code == 429:
                _, delay, ids = _quota_info(resp)
                if _is_daily(delay, ids):
                    detail = ", ".join(i for i in ids if i) or f"sunucu {delay:.0f} sn bekleme istedi"
                    raise QuotaExhausted(
                        f"Günlük kota doldu ({detail}). Kota Pasifik saatiyle gece yarısı sıfırlanır "
                        "(Türkiye'de yaklaşık 10:00-11:00). Sonra aynı komutu tekrar çalıştır.")
                if attempt < max_retries:
                    time.sleep(delay + 1 if delay is not None else 2 ** attempt)
                    continue
            elif resp.status_code in RETRY_STATUS and attempt < max_retries:
                time.sleep(2 ** attempt)  # 1, 2, 4 sn
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
