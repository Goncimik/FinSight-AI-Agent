"""compute_metrics: finansal oranları Python ile hesaplar. LLM hesap yapmaz.

Formüller ve girdi kavramları metrics.yaml içindedir. Rakamlar get_company_facts'ten
doğrudan koda akar; LLM'in elinden geçmez, bu yüzden değiştirilemez ya da yanlış
kopyalanamaz. Her sonuç formülü, girdileri ve kaynak accn'ları taşır.

'years' parametresi her zaman altta yatan MALİ YIL VERİSİ sayısıdır:
- margin: years adet marj değeri
- growth: years yıllık veriden years-1 büyüme değeri
- cagr:   years yıllık veri -> years-1 dönem
"""
from datetime import date

from src.tools.company_facts import get_company_facts, load_metrics


def cagr(start: float, end: float, years: int) -> float:
    """Bileşik yıllık büyüme. years = dönem sayısı (ör. 2020→2024 için 4)."""
    if start <= 0 or end < 0 or years <= 0:
        raise ValueError("CAGR tanımsız: başlangıç <= 0, bitiş < 0 veya dönem <= 0")
    return (end / start) ** (1 / years) - 1


def _is_consecutive(prev: dict, cur: dict, period: dict) -> bool:
    gap = (date.fromisoformat(cur["end"]) - date.fromisoformat(prev["end"])).days
    return period["min_days"] <= gap <= period["max_days"]


def _accns(*rows: dict) -> list[str]:
    seen: list[str] = []
    for r in rows:
        if r["accn"] not in seen:
            seen.append(r["accn"])
    return seen


def _needed_concepts(spec: dict) -> list[str]:
    if spec["kind"] == "margin":
        return [spec["numerator"], spec["denominator"]]
    return [spec["input"]]


def compute_metric_from_rows(metric: str, spec: dict, rows_by_concept: dict, period: dict) -> dict:
    """Saf hesap fonksiyonu: dosya okumaz, ağa çıkmaz; birim testi buna yazılır."""
    units = {r["unit"] for rows in rows_by_concept.values() for r in rows}
    if len(units) > 1:
        return {"error": f"Birim uyuşmazlığı: {sorted(units)}. Oran hesaplanmadı."}

    kind = spec["kind"]
    results: list[dict] = []
    skipped: list[dict] = []

    if kind == "margin":
        num_c, den_c = spec["numerator"], spec["denominator"]
        num = {r["end"]: r for r in rows_by_concept[num_c]}
        den = {r["end"]: r for r in rows_by_concept[den_c]}
        for end in sorted(set(num) | set(den)):
            if end not in num or end not in den:
                missing = num_c if end not in num else den_c
                skipped.append({"end": end, "reason": f"{missing} verisi yok"})
                continue
            n, d = num[end], den[end]
            if d["value"] == 0:
                skipped.append({"end": end, "reason": f"{den_c} sıfır, oran tanımsız"})
                continue
            results.append({
                "fiscal_year": n["fiscal_year"], "end": end,
                "value": n["value"] / d["value"],
                "inputs": {num_c: n["value"], den_c: d["value"]},
                "source_accns": _accns(n, d),
            })

    elif kind == "growth":
        c = spec["input"]
        rows = sorted(rows_by_concept[c], key=lambda r: r["end"])
        for prev, cur in zip(rows, rows[1:]):
            if not _is_consecutive(prev, cur, period):
                skipped.append({"end": cur["end"], "reason": "önceki dönemle ardışık değil (veri boşluğu)"})
                continue
            if prev["value"] <= 0:
                skipped.append({"end": cur["end"], "reason": f"önceki dönem {c} <= 0, büyüme tanımsız"})
                continue
            results.append({
                "fiscal_year": cur["fiscal_year"], "end": cur["end"],
                "value": cur["value"] / prev["value"] - 1,
                "inputs": {f"{c}_previous": prev["value"], c: cur["value"]},
                "source_accns": _accns(prev, cur),
            })

    elif kind == "cagr":
        c = spec["input"]
        rows = sorted(rows_by_concept[c], key=lambda r: r["end"])
        if len(rows) < 2:
            return {"error": "CAGR için en az 2 mali yıllık veri gerekir."}
        for prev, cur in zip(rows, rows[1:]):
            if not _is_consecutive(prev, cur, period):
                return {"error": f"{cur['end']} öncesinde veri boşluğu var; CAGR hesaplanamaz."}
        periods = len(rows) - 1
        try:
            value = cagr(rows[0]["value"], rows[-1]["value"], periods)
        except ValueError as exc:
            return {"error": str(exc)}
        results.append({
            "fiscal_year": rows[-1]["fiscal_year"], "end": rows[-1]["end"], "value": value,
            "inputs": {f"{c}_start": rows[0]["value"], f"{c}_end": rows[-1]["value"],
                       "start_end": rows[0]["end"], "periods": periods},
            "source_accns": _accns(*rows),
        })

    else:
        return {"error": f"'{metric}' için bilinmeyen kind '{kind}'."}

    if not results:
        return {"error": f"'{metric}' için hesaplanabilir dönem bulunamadı.", "skipped": skipped}
    out = {"metric": metric, "unit": spec["unit"], "formula": spec["formula"], "results": results}
    if skipped:
        out["skipped"] = skipped
    return out


def compute_metrics(cik: int, metric: str, years: int = 5) -> dict:
    """Bir şirket için büyüme, marj veya CAGR hesaplar (hesabı Python yapar).

    Args:
        cik: Şirketin CIK numarası (tam sayı; ör. Apple için 320193).
        metric: 'revenue_growth', 'gross_margin', 'operating_margin', 'net_margin'
            veya 'revenue_cagr'.
        years: Kullanılacak mali yıl verisi sayısı (2-20, varsayılan 5). Büyüme için
            years-1 değer, CAGR için years-1 dönem üzerinden tek değer döner.

    Returns:
        Başarılıysa {'cik', 'metric', 'unit', 'formula', 'results': [...]}. Her sonuçta
        fiscal_year, end, value (oran; 0.25 = %25), inputs (kullanılan ham değerler) ve
        source_accns (kaynak raporlar) bulunur. Hesaplanamayan dönemler 'skipped'
        listesinde nedeniyle yazılır. Hata varsa {'error': '...'} döner.
    """
    metrics = load_metrics()
    specs = metrics["metrics"]
    if metric not in specs:
        return {"error": f"Bilinmeyen metric '{metric}'. Geçerli değerler: {sorted(specs)}."}
    if isinstance(years, bool) or not isinstance(years, int) or not 2 <= years <= 20:
        return {"error": "years 2 ile 20 arasında bir tam sayı olmalı."}

    spec = specs[metric]
    rows_by_concept: dict[str, list[dict]] = {}
    for concept in _needed_concepts(spec):
        facts = get_company_facts(cik, concept, years)
        if "error" in facts:
            return {"error": f"'{concept}' verisi alınamadı: {facts['error']}"}
        rows_by_concept[concept] = facts["rows"]

    out = compute_metric_from_rows(metric, spec, rows_by_concept, metrics["period"])
    return out if "error" in out else {"cik": cik, **out}


if __name__ == "__main__":
    import sys

    cik_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 320193
    for name in load_metrics()["metrics"]:
        res = compute_metrics(cik_arg, name, years=5)
        print(f"\n{name}  ({res.get('formula', '')})")
        if "error" in res:
            print("  HATA:", res["error"])
            continue
        for r in res["results"]:
            print(f"  FY{r['fiscal_year']}  {r['end']}  {r['value'] * 100:>7.2f}%  "
                  f"{', '.join(r['source_accns'])}")
        if "skipped" in res:
            print("  atlanan:", res["skipped"])