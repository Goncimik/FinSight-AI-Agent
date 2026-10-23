"""resolve_ticker_to_cik: borsa kodunu (ticker) CIK numarasına ve şirket adına çevirir.

Kaynak: SEC'in company_tickers.json dosyası (scripts/download_edgar.py indirir).
Bu tool, LLM'in CIK numarasını hafızasından uydurmasını engeller: CIK her zaman
SEC listesinden gelir. Şirket adıyla arama yapmaz; yalnızca tam ticker eşleşmesi.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TICKERS_PATH = ROOT / "data" / "raw" / "company_tickers.json"


def resolve_ticker_to_cik(ticker: str) -> dict:
    """Bir borsa kodunun (ticker) SEC CIK numarasını ve şirket adını bulur.

    Args:
        ticker: Borsa kodu, büyük/küçük harf fark etmez (ör. 'AAPL' ya da 'aapl').

    Returns:
        Başarılıysa {'ticker': 'AAPL', 'cik': 320193, 'name': 'Apple Inc.'}.
        Ticker bulunamazsa ya da girdi geçersizse {'error': '...'} döner.
    """
    if not isinstance(ticker, str) or not ticker.strip():
        return {"error": "ticker boş olmayan bir metin olmalı (ör. 'AAPL')."}
    if not TICKERS_PATH.exists():
        return {"error": "company_tickers.json bulunamadı. "
                         "Önce scripts/download_edgar.py çalıştırılmalı."}

    wanted = ticker.strip().upper()
    data = json.loads(TICKERS_PATH.read_text(encoding="utf-8"))
    for row in data.values():
        if row["ticker"].upper() == wanted:
            return {"ticker": wanted, "cik": int(row["cik_str"]), "name": row["title"]}
    return {"error": f"'{wanted}' ticker'ı SEC listesinde bulunamadı. "
                     "Ticker'ı kontrol et (ör. 'AAPL'); şirket adıyla arama desteklenmez."}


if __name__ == "__main__":
    import sys

    print(resolve_ticker_to_cik(sys.argv[1] if len(sys.argv) > 1 else "AAPL"))