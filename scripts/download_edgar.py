"""SEC EDGAR indirme scripti.

ticker -> CIK, submissions, companyfacts ve son N yılın 10-K HTML dosyaları.
Ham veri data/raw/ altına yazılır (.gitignore'da, repoya girmez).
Var olan dosya tekrar indirilmez (disk cache). İstekler throttle edilir.

Kullanım:
    python scripts/download_edgar.py --tickers AAPL --years 5
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

RAW = Path("data/raw")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
MIN_INTERVAL = 0.2  # en fazla ~5 istek/sn (SEC sınırı 10 istek/sn)
_last_request = 0.0


def get_user_agent() -> str:
    ua = os.getenv("SEC_USER_AGENT", "").strip().strip('"')
    if not ua or "ornek.com" in ua:
        sys.exit("HATA: .env içinde SEC_USER_AGENT gerçek ad ve e-posta ile doldurulmalı.")
    return ua


def fetch(client: httpx.Client, url: str, dest: Path) -> None:
    """url'yi dest'e indirir. dest zaten doluysa ağa çıkmaz."""
    global _last_request
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [cache]     {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        r = client.get(url)
        if r.status_code == 200:
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(r.content)
            tmp.replace(dest)
            print(f"  [indirildi] {dest} ({len(r.content) / 1024:.0f} KB)")
            return
        if r.status_code in (403, 429, 500, 502, 503):
            backoff = 2 ** attempt
            print(f"  [uyarı] HTTP {r.status_code}; {backoff} sn sonra tekrar denenecek")
            time.sleep(backoff)
            continue
        raise RuntimeError(f"{url} -> HTTP {r.status_code}")
    raise RuntimeError(
        f"{url} indirilemedi. 403 devam ediyorsa SEC_USER_AGENT değerini kontrol et."
    )


def resolve_ticker(ticker: str) -> tuple[int, str]:
    data = json.loads((RAW / "company_tickers.json").read_text(encoding="utf-8"))
    for row in data.values():
        if row["ticker"].upper() == ticker:
            return int(row["cik_str"]), row["title"]
    sys.exit(f"HATA: {ticker} company_tickers.json içinde bulunamadı.")


def extract_filings(block: dict) -> list[dict]:
    out = []
    for i, form in enumerate(block["form"]):
        if form in ("10-K", "10-K/A"):
            out.append({
                "form": form,
                "accession": block["accessionNumber"][i],
                "document": block["primaryDocument"][i],
                "filing_date": block["filingDate"][i],
                "report_date": block["reportDate"][i] or block["filingDate"][i],
            })
    return out


def collect_filings(client: httpx.Client, cik: int, years: int) -> list[dict]:
    sub_path = RAW / "submissions" / f"CIK{cik:010d}.json"
    fetch(client, f"https://data.sec.gov/submissions/CIK{cik:010d}.json", sub_path)
    sub = json.loads(sub_path.read_text(encoding="utf-8"))
    filings = extract_filings(sub["filings"]["recent"])
    # "recent" yeterli 10-K içermiyorsa SEC'in ek submissions dosyalarına bak
    for extra in sub["filings"].get("files", []):
        if sum(f["form"] == "10-K" for f in filings) >= years:
            break
        extra_path = RAW / "submissions" / extra["name"]
        fetch(client, f"https://data.sec.gov/submissions/{extra['name']}", extra_path)
        filings += extract_filings(json.loads(extra_path.read_text(encoding="utf-8")))
    return filings


def process_ticker(client: httpx.Client, ticker: str, years: int) -> None:
    cik, name = resolve_ticker(ticker)
    print(f"\n{ticker}: {name} (CIK {cik:010d})")
    base = RAW / ticker

    fetch(
        client,
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
        base / "companyfacts.json",
    )

    filings = collect_filings(client, cik, years)
    tenk = sorted(
        (f for f in filings if f["form"] == "10-K"),
        key=lambda f: f["report_date"],
        reverse=True,
    )[:years]
    amendments = [f for f in filings if f["form"] == "10-K/A"]

    for f in tenk:
        accn = f["accession"].replace("-", "")
        suffix = Path(f["document"]).suffix or ".htm"
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{f['document']}"
        dest = base / "10-K" / f"{f['report_date']}_{accn}{suffix}"
        fetch(client, url, dest)
        f["file"] = str(dest)

    manifest = {"ticker": ticker, "name": name, "cik": cik,
                "10-K": tenk, "10-K/A (yalnızca kayıt)": amendments}
    (base / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  10-K: {len(tenk)} dosya; bulunan 10-K/A kaydı: {len(amendments)}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="SEC EDGAR veri indirici")
    parser.add_argument("--tickers", required=True, help="Virgülle ayrılmış: AAPL,MSFT")
    parser.add_argument("--years", type=int, default=5, help="Kaç yıllık 10-K")
    args = parser.parse_args()

    ua = get_user_agent()
    print("User-Agent ayarlandı (değer log'a yazılmaz).")
    with httpx.Client(headers={"User-Agent": ua}, timeout=30, follow_redirects=True) as client:
        fetch(client, TICKERS_URL, RAW / "company_tickers.json")
        for t in args.tickers.split(","):
            process_ticker(client, t.strip().upper(), args.years)
    print("\nBitti.")


if __name__ == "__main__":
    main()