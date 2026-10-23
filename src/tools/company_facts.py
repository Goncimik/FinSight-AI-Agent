"""get_company_facts: SEC XBRL companyfacts verisinden yıllık değerleri çeker.

Kurallar (metrics.yaml ile uyumlu):
- Yalnızca 10-K / 10-K/A kaynaklı, yaklaşık 1 yıllık süreli (350-380 gün) kayıtlar.
- Yıl, 'fy' alanından değil dönem sonu (end) tarihinden alınır; 'fy' kaydı taşıyan
  raporun mali yılıdır, verinin ait olduğu yıl değildir.
- Aynı dönem ve aynı etiket için birden çok kayıt varsa en son filed tarihlisi seçilir
  (sonraki raporlar önceki yılları yeniden raporlar; 10-K/A düzeltmeleri de böyle gelir).
- Etiketler öncelik sırasıyla dönem bazında denenir; ilk dolu etiket seçilir ve
  çıktıya yazılır.
- Değerler ham birimdedir (USD). Bin/milyon dönüşümü yalnızca gösterimde yapılır.

Bilinen sınır: fiscal_year = dönem sonu tarihinin yılıdır. Mali yılı Ocak başında
biten şirketlerde (ör. 28 Ocak 2023 = FY2022) bu kural yanlış yıl verir.
"""
import json
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "raw"
METRICS_PATH = ROOT / "metrics.yaml"


def load_metrics() -> dict:
    return yaml.safe_load(METRICS_PATH.read_text(encoding="utf-8"))


def _find_facts_file(cik: int) -> Path | None:
    """data/raw/*/manifest.json içinde cik'i bulur, companyfacts.json yolunu döndürür."""
    for manifest in DATA_DIR.glob("*/manifest.json"):
        info = json.loads(manifest.read_text(encoding="utf-8"))
        if info.get("cik") == cik:
            path = manifest.parent / "companyfacts.json"
            return path if path.exists() else None
    return None


def _is_annual(record: dict, period: dict) -> bool:
    if not record.get("start") or record.get("form") not in period["forms"]:
        return False
    days = (date.fromisoformat(record["end"]) - date.fromisoformat(record["start"])).days
    return period["min_days"] <= days <= period["max_days"]


def get_company_facts(cik: int, concept: str, years: int = 5) -> dict:
    """Bir şirketin yıllık finansal değerlerini SEC XBRL verisinden getirir.

    Args:
        cik: Şirketin CIK numarası (tam sayı; ör. Apple için 320193).
        concept: Metrik adı: 'revenue', 'net_income' veya 'operating_income'.
        years: Son kaç mali yılın getirileceği (1-20, varsayılan 5).

    Returns:
        Başarılıysa {'cik', 'concept', 'unit', 'rows': [...]}. Her satırda fiscal_year,
        start, end, value (ham USD), unit, tag (kullanılan XBRL etiketi), form, filed,
        accn (kaynak) bulunur. Hata varsa {'error': '...'} döner.
    """
    metrics = load_metrics()
    concepts = metrics["concepts"]
    if concept not in concepts:
        return {"error": f"Bilinmeyen concept '{concept}'. Geçerli değerler: {sorted(concepts)}."}
    if isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 20:
        return {"error": "years 1 ile 20 arasında bir tam sayı olmalı."}

    facts_path = _find_facts_file(cik)
    if facts_path is None:
        return {"error": f"CIK {cik} için indirilmiş veri bulunamadı. "
                         "Önce scripts/download_edgar.py çalıştırılmalı."}

    spec = concepts[concept]
    unit = spec["unit"]
    us_gaap = json.loads(facts_path.read_text(encoding="utf-8"))["facts"]["us-gaap"]

    chosen: dict[str, tuple[str, dict]] = {}  # dönem sonu -> (etiket, kayıt)
    for tag in spec["tags"]:  # öncelik sırası: yüksek öncelikli etiket dönemi önce doldurur
        latest: dict[str, dict] = {}
        for rec in us_gaap.get(tag, {}).get("units", {}).get(unit, []):
            if not _is_annual(rec, metrics["period"]):
                continue
            current = latest.get(rec["end"])
            if current is None or rec["filed"] > current["filed"]:
                latest[rec["end"]] = rec
        for end, rec in latest.items():
            chosen.setdefault(end, (tag, rec))

    rows = [
        {
            "fiscal_year": int(end[:4]),
            "start": rec["start"],
            "end": end,
            "value": rec["val"],
            "unit": unit,
            "tag": tag,
            "form": rec["form"],
            "filed": rec["filed"],
            "accn": rec["accn"],
        }
        for end, (tag, rec) in sorted(chosen.items())
    ][-years:]

    if not rows:
        return {"error": f"'{concept}' için {unit} birimli yıllık kayıt bulunamadı "
                         f"(denenen etiketler: {spec['tags']})."}
    result = {"cik": cik, "concept": concept, "unit": unit, "rows": rows}
    if len(rows) < years:
        result["note"] = f"İstenen {years} yıl yerine {len(rows)} yıl bulundu."
    return result


if __name__ == "__main__":
    import sys

    cik_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 320193
    for name in ("revenue", "net_income", "operating_income"):
        out = get_company_facts(cik_arg, name, years=5)
        print(f"\n{name}")
        if "error" in out:
            print("  HATA:", out["error"])
            continue
        print(f"  {'FY':<6}{'dönem sonu':<12}{'değer (milyon USD)':>20}  {'form':<7}{'accn':<22}etiket")
        for r in out["rows"]:
            print(f"  {r['fiscal_year']:<6}{r['end']:<12}{r['value'] / 1e6:>20,.0f}  "
                  f"{r['form']:<7}{r['accn']:<22}{r['tag']}")