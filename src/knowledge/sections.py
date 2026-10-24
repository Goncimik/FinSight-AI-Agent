"""10-K HTML dosyalarını temizler ve Item 1 / 1A / 7 bölümlerine ayırır.

Rehberin uyardığı iki tuzak:
1. 'Item 7' içindekiler tablosunda da geçer; gerçek bölüm başlangıcı bulunmalı.
2. iXBRL gizli blokları (<ix:header>) metne karışmamalı.

Yöntem: satır başındaki tüm 'Item N.' başlıkları sınır sayılır. İstenen bölüm için
(başlık adı doğrulanmış) adaylar arasından EN UZUN dilim seçilir; içindekiler tablosundaki
aday bir sonraki başlığa kadar birkaç karakterdir, gerçek bölüm binlerce karakterdir.
Çıktı data/cache/sections/ altına yazılır (.gitignore'da; yeniden üretilebilir).
"""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "cache" / "sections"

MIN_CHARS = 2000  # bundan kısa dilim gerçek bölüm sayılmaz (büyük olasılıkla içindekiler)

# bölüm anahtarı -> (başlıktaki item numarası, başlık adında aranacak sözcük)
WANTED = {
    "item_1": ("1", "business"),
    "item_1a": ("1A", "risk factors"),
    "item_7": ("7", "management"),
}

HEADING = re.compile(r"^[ \t]*item[ \t]+(\d{1,2}[a-c]?)\b", re.IGNORECASE | re.MULTILINE)


def html_to_text(raw: str) -> str:
    """HTML'i satır yapısını koruyarak düz metne çevirir; gizli iXBRL bloklarını atar."""
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|h[1-6]|li|table)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(raw).replace("\xa0", " ")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def extract_sections(text: str) -> tuple[dict, dict]:
    """Metinden Item 1 / 1A / 7 bölümlerini çıkarır.

    Returns:
        (sections, failures): sections {'item_1': metin, ...}; failures {'item_7': neden, ...}
    """
    heads = [(m.start(), m.group(1).upper(), text[m.end(): m.end() + 120].lower())
             for m in HEADING.finditer(text)]
    slices = []  # (item_id, lookahead, dilim_metni)
    for i, (start, item_id, look) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        slices.append((item_id, look, text[start:end].strip()))

    sections: dict[str, str] = {}
    failures: dict[str, str] = {}
    for key, (item_id, keyword) in WANTED.items():
        candidates = [body for sid, look, body in slices if sid == item_id and keyword in look]
        if not candidates:
            failures[key] = f"'Item {item_id}' başlığı ('{keyword}') bulunamadı"
            continue
        best = max(candidates, key=len)
        if len(best) < MIN_CHARS:
            failures[key] = (f"başlık bulundu ama en uzun dilim yalnızca {len(best)} karakter "
                             "(muhtemelen içindekiler tablosu)")
            continue
        sections[key] = best
    return sections, failures


def process_ticker(ticker: str) -> list[dict]:
    """manifest.json'daki 10-K'ları işler, her biri için bir JSON yazar; rapor listesi döner."""
    base = RAW_DIR / ticker
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    out_dir = OUT_DIR / ticker
    out_dir.mkdir(parents=True, exist_ok=True)

    reports = []
    for f in manifest["10-K"]:
        accn_nodash = f["accession"].replace("-", "")
        record = {
            "ticker": ticker, "cik": manifest["cik"], "form": f["form"],
            "fy": int(f["report_date"][:4]),  # dönem sonu yılı (metrics.yaml kuralıyla aynı)
            "report_date": f["report_date"], "filed": f["filing_date"], "accn": f["accession"],
            "url": f"https://www.sec.gov/Archives/edgar/data/{manifest['cik']}/"
                   f"{accn_nodash}/{f['document']}",
        }
        text = html_to_text((ROOT / f["file"]).read_text(encoding="utf-8", errors="replace"))
        sections, failures = extract_sections(text)
        record["sections"], record["failures"] = sections, failures
        path = out_dir / f"{f['report_date']}_{accn_nodash}.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        reports.append({"fy": record["fy"], "report_date": f["report_date"], "accn": f["accession"],
                        "chars": {k: len(v) for k, v in sections.items()}, "failures": failures})
    return reports


if __name__ == "__main__":
    ticker_arg = (sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper()
    results = process_ticker(ticker_arg)
    ok = 0
    for r in results:
        sizes = "  ".join(f"{k}: {v:,}" for k, v in r["chars"].items())
        print(f"FY{r['fy']}  {r['report_date']}  {r['accn']}  {sizes}")
        for key, why in r["failures"].items():
            print(f"    BAŞARISIZ {key}: {why}")
        ok += not r["failures"]
    print(f"\nTüm bölümleri ayrılan 10-K: {ok}/{len(results)}")