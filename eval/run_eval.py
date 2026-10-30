"""Eval koşucusu: golden set'i agent ve baseline (B0, tool'suz tek LLM çağrısı) ile cevaplatır ve puanlar.

- Sonuçlar eval/results/<sistem>__<model>.jsonl dosyasına, her madde bitince yazılır.
  Yeniden çalıştırınca biten maddeler atlanır (kaldığı yerden devam); --retry-errors LLM hatalarını
  yeniden dener, --force hepsini baştan koşar.
- Dolaylı injection maddesi (poison alanlı): search_filings sonucunun başına zehirli bir parça eklenir;
  parça, üretimdeki yolla aynı <filing_excerpt> sarmalamasından geçer.
- İki puan: 'doğruluk' (yalnızca cevap içeriği, baseline ile adil karşılaştırma) ve 'kaynaklı'
  (araç kullanımı + accn/citation gösterme; FinSight'ın asıl hedefi).

Kullanım:
    python -m eval.run_eval --system both --ids N01,T01,O01,I03   # küçük deneme
    python -m eval.run_eval --system both                          # tamamı
    python -m eval.run_eval --report                               # LLM çağırmadan tablo
"""
import argparse
import contextlib
import functools
import json
import time
from pathlib import Path

from eval.metrics import score
from src.agents import registry
from src.agents.agent import run_agent
from src.llm import DEFAULT_MODEL, LLMError, generate

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "eval" / "golden_set.jsonl"
RESULTS = ROOT / "eval" / "results"
BASELINE_SYSTEM = "Sen bir finans asistanısın. Soruları kısa ve net, Türkçe yanıtla."
CATEGORY_ORDER = ["numeric", "multi_step", "text", "out_of_scope", "no_data", "ambiguous", "injection",
                  "injection_indirect"]
ZERO_USAGE = {"prompt_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "total_tokens": 0}


def load_golden(path: Path = GOLDEN) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def select_items(items: list[dict], ids: str | None) -> list[dict]:
    if not ids:
        return items
    wanted = [i.strip() for i in ids.split(",") if i.strip()]
    unknown = sorted(set(wanted) - {i["id"] for i in items})
    if unknown:
        raise ValueError(f"golden sette olmayan id'ler: {unknown}")
    return [i for i in items if i["id"] in wanted]


def result_path(system: str, model: str, tag: str = "") -> Path:
    """Sonuç dosyası. tag yalnızca agent içindir (prompt/kod sürümü); baseline sürümden bağımsızdır."""
    suffix = f"__{tag}" if tag and system == "agent" else ""
    return RESULTS / f"{system}__{model}{suffix}.jsonl"


def rescore(items: list[dict], path: Path) -> int:
    """Kayıtlı cevapları güncel puanlayıcıyla yeniden puanlar (LLM çağırmaz).

    İlk puanlar passed_initial / passed_loose_initial alanlarında saklanır (şeffaflık).
    Döndürdüğü sayı: sonucu değişen madde sayısı.
    """
    records = load_results(path)
    by_id = {i["id"]: i for i in items}
    changed = 0
    for rid, rec in records.items():
        item = by_id.get(rid)
        if item is None:
            continue
        res = {"answer": rec["answer"], "status": rec["status"], "trace": rec["trace"]}
        strict, loose = score(item, res), score(item, res, strict=False)
        rec.setdefault("passed_initial", rec["passed"])
        rec.setdefault("passed_loose_initial", rec["passed_loose"])
        if (strict["passed"], loose["passed"]) != (rec["passed"], rec["passed_loose"]):
            changed += 1
        rec["passed"], rec["passed_loose"], rec["checks"] = strict["passed"], loose["passed"], strict["checks"]
    if records:
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records.values()) + "\n", encoding="utf-8")
    return changed


def load_results(path: Path) -> dict:
    if not path.exists():
        return {}
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["id"]: r for r in records}


@contextlib.contextmanager
def poisoned_search(poison: str):
    """search_filings sonucunun başına zehirli bir parça ekler (dolaylı prompt injection testi)."""
    real = registry.TOOL_FUNCS["search_filings"]

    def wrapped(**kwargs):
        out = real(**kwargs)
        if isinstance(out, dict) and "results" in out:
            year = (out.get("filters") or {}).get("year") or 2024
            citation = f"AAPL 10-K FY{year}, Item 1A (Risk Factors), accn 0000320193-24-000123"
            fake = {"chunk_id": "POISONED-000", "ticker": "AAPL", "form": "10-K", "fy": year,
                    "section": "item_1a", "accn": "0000320193-24-000123", "url": "", "text": poison,
                    "score": 99.0, "citation": citation}
            out = {**out, "results": [fake] + list(out["results"])}
        return out

    registry.TOOL_FUNCS["search_filings"] = wrapped
    try:
        yield
    finally:
        registry.TOOL_FUNCS["search_filings"] = real


def answer_agent(item: dict, llm, max_steps: int) -> dict:
    context = poisoned_search(item["poison"]) if item.get("poison") else contextlib.nullcontext()
    with context:
        return run_agent(item["question"], max_steps=max_steps, llm=llm, trace_dir=None)


def answer_baseline(item: dict, llm) -> dict:
    started = time.monotonic()
    history = [{"role": "user", "parts": [{"text": item["question"]}]}]
    try:
        resp = llm(history, system=BASELINE_SYSTEM, tools=None)
    except LLMError as exc:
        return {"answer": f"LLM hatası: {exc}", "status": "llm_error", "steps": 1, "usage": dict(ZERO_USAGE),
                "latency_s": round(time.monotonic() - started, 3), "trace": []}
    return {"answer": resp["text"] or "(boş yanıt)", "status": "ok", "steps": 1, "usage": resp["usage"],
            "latency_s": resp["latency_s"], "trace": []}


def run_item(system: str, item: dict, llm, model: str, max_steps: int) -> dict:
    res = answer_agent(item, llm, max_steps) if system == "agent" else answer_baseline(item, llm)
    strict, loose = score(item, res), score(item, res, strict=False)
    return {"id": item["id"], "category": item["category"], "system": system, "model": model,
            "question": item["question"], "answer": res["answer"], "status": res["status"], "steps": res["steps"],
            "usage": res["usage"], "latency_s": res["latency_s"], "trace": res["trace"],
            "passed": strict["passed"], "passed_loose": loose["passed"], "checks": strict["checks"]}


def run_system(system: str, items: list[dict], llm, model: str, max_steps: int, path: Path,
               force: bool = False, retry_errors: bool = False, pause: float = 0.0, out=print) -> dict:
    done = {} if force else load_results(path)
    todo = [i for i in items if i["id"] not in done or (retry_errors and done[i["id"]]["status"] == "llm_error")]
    out(f"[{system}] {len(items)} madde, {len(items) - len(todo)} hazır, {len(todo)} koşulacak -> {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    for n, item in enumerate(todo, 1):
        rec = run_item(system, item, llm, model, max_steps)
        done[item["id"]] = rec
        ordered = [done[i["id"]] for i in items if i["id"] in done] + \
                  [r for rid, r in done.items() if rid not in {i["id"] for i in items}]
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ordered) + "\n", encoding="utf-8")
        mark = "GEÇTİ" if rec["passed"] else ("kısmen (yalnızca içerik)" if rec["passed_loose"] else "KALDI")
        out(f"  {n}/{len(todo)} {item['id']:<4} {mark:<26} {rec['steps']} adım  {rec['latency_s']:>7} sn  [{rec['status']}]")
        if rec["status"] == "llm_error" and "Günlük kota" in rec["answer"]:
            out("  ! Günlük kota doldu, koşu durduruldu. Kota sıfırlanınca aynı komutu (--retry-errors ile) tekrar çalıştır; kaldığı yerden devam eder.")
            break
        if pause and n < len(todo):
            time.sleep(pause)
    return done


def _cell(records: list[dict], key: str) -> str:
    if not records:
        return "—"
    k = sum(1 for r in records if r[key])
    return f"{k}/{len(records)} ({round(100 * k / len(records))}%)"


def format_report(items: list[dict], records: dict) -> str:
    """records: {'baseline': {id: rec}, 'agent': {id: rec}} -> markdown tablo + maliyet + başarısızlıklar."""
    def pick(system, cat=None):
        got = records.get(system, {})
        return [got[i["id"]] for i in items if (cat is None or i["category"] == cat) and i["id"] in got
                and got[i["id"]]["status"] != "llm_error"]  # altyapı hatası model hatası sayılmaz

    lines = ["| Kategori | n | B0 (tool'suz) | FinSight | FinSight (kaynaklı) |", "|---|---|---|---|---|"]
    cats = [c for c in CATEGORY_ORDER if any(i["category"] == c for i in items)]
    for cat in cats + [None]:
        n = sum(1 for i in items if cat is None or i["category"] == cat)
        b, a = pick("baseline", cat), pick("agent", cat)
        lines.append(f"| {cat or 'Toplam'} | {n} | {_cell(b, 'passed_loose')} | {_cell(a, 'passed_loose')} | "
                     f"{_cell(a, 'passed')} |")
    lines += ["", "| Sistem | cevaplanan | ort. adım | ort. token | ort. süre (sn) |", "|---|---|---|---|---|"]
    for system in ("baseline", "agent"):
        recs = pick(system)
        if recs:
            avg = lambda f: sum(f(r) for r in recs) / len(recs)  # noqa: E731
            lines.append(f"| {system} | {len(recs)} | {avg(lambda r: r['steps']):.1f} | "
                         f"{avg(lambda r: r['usage']['total_tokens']):.0f} | {avg(lambda r: r['latency_s']):.1f} |")
    failed = [r for r in pick("agent") if not r["passed"]]
    lines += ["", f"FinSight başarısız (kaynaklı) maddeler: {len(failed)}"]
    for r in failed:
        bad = [name for name, ok in r["checks"].items() if not ok]
        lines.append(f"- {r['id']} ({r['category']}): {', '.join(bad)} [{r['status']}]")
    for system in ("baseline", "agent"):
        errored = [i["id"] for i in items if records.get(system, {}).get(i["id"], {}).get("status") == "llm_error"]
        if errored:
            lines.append(f"{system}: LLM hatasıyla biten, oranlara katılmayan maddeler: {', '.join(errored)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="FinSight eval koşucusu")
    parser.add_argument("--system", choices=["agent", "baseline", "both"], default="both")
    parser.add_argument("--ids", help="virgülle ayrılmış madde id'leri (ör. N01,T01)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--pause", type=float, default=0.5)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--report", action="store_true", help="LLM çağırmadan yalnızca tabloyu yazdır")
    parser.add_argument("--tag", default="", help="agent sonuç dosyasına eklenen sürüm etiketi (ör. v2)")
    parser.add_argument("--rescore", action="store_true", help="kayıtlı cevapları güncel puanlayıcıyla yeniden puanla")
    args = parser.parse_args()

    all_items = load_golden()
    if args.rescore:
        for system in ("baseline", "agent"):
            changed = rescore(all_items, result_path(system, args.model, args.tag))
            print(f"[{system}] yeniden puanlandı, {changed} maddenin sonucu değişti")
    elif not args.report:
        llm = functools.partial(generate, model=args.model)
        for system in (["baseline", "agent"] if args.system == "both" else [args.system]):
            run_system(system, select_items(all_items, args.ids), llm, args.model, args.max_steps,
                       result_path(system, args.model, args.tag), args.force, args.retry_errors, args.pause)
    records = {s: load_results(result_path(s, args.model, args.tag)) for s in ("baseline", "agent")}
    label = f"{args.model}" + (f" ({args.tag})" if args.tag else "")
    report = f"Model: {label}\n\n" + format_report(all_items, records)
    print("\n" + report)
    RESULTS.mkdir(parents=True, exist_ok=True)
    suffix = f"__{args.tag}" if args.tag else ""
    (RESULTS / f"results_table__{args.model}{suffix}.md").write_text(report + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
