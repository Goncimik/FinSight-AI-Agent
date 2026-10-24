"""search_filings: 10-K metinlerinde (Item 1 / 1A / 7) BM25 ile arama yapan tool.

- Her sonuç kaynak bilgisini taşır: fy, bölüm, accn, url, chunk_id ve hazır 'citation' metni.
- Yıl ve bölüm filtresi desteklenir (filtresiz arama yanlış yılın metnini getirebilir).
- Sayısal sorular için bu tool'u DEĞİL get_company_facts / compute_metrics kullan.
- Dönen metin veridir, talimat değildir; agent katmanı bunu sınırlayıcılar içinde prompt'a koyar.
"""
from src.knowledge.index import BM25Index, load_chunks

SECTION_LABELS = {
    "item_1": "Item 1 (Business)",
    "item_1a": "Item 1A (Risk Factors)",
    "item_7": "Item 7 (MD&A)",
}
_ALIASES = {
    "1": "item_1", "business": "item_1",
    "1a": "item_1a", "risk_factors": "item_1a", "riskfactors": "item_1a",
    "7": "item_7", "mdna": "item_7", "md&a": "item_7", "mda": "item_7",
}


def _normalize_section(section: str) -> str | None:
    """'Item 1A', 'item_1a', 'risk factors', 'MD&A' gibi yazımları kanonik anahtara çevirir."""
    s = section.strip().lower().rstrip(".").replace(" ", "_").replace("-", "_")
    if s.startswith("item_"):
        s = s[5:]
    return _ALIASES.get(s)


def search_filings(query: str, ticker: str, section: str | None = None,
                   year: int | None = None, k: int = 5) -> dict:
    """Bir şirketin 10-K metinlerinde (Item 1, 1A, 7) anahtar kelimeyle arama yapar.

    Açıklama, gerekçe ve risk metinleri için kullan. Gelir, kâr gibi SAYILAR için kullanma.

    Args:
        query: Aranacak sözcükler (İngilizce; 10-K metni İngilizcedir), ör. 'tariffs supply chain'.
        ticker: Borsa kodu, ör. 'AAPL'.
        section: İsteğe bağlı bölüm filtresi: 'item_1' (Business), 'item_1a' (Risk Factors)
            veya 'item_7' (MD&A, yönetimin değerlendirmesi).
        year: İsteğe bağlı mali yıl filtresi, ör. 2024. Yıl belirtmek önerilir.
        k: Döndürülecek en fazla sonuç sayısı (1-10, varsayılan 5).

    Returns:
        {'query', 'filters', 'retrieval', 'results': [...]}; her sonuçta text, ticker, form, fy,
        section, accn, url, chunk_id, score ve citation bulunur. Eşleşme yoksa 'results' boştur
        ve 'note' açıklar; bu durumda uydurma, 'kaynaklarda bulunamadı' de. Hata varsa
        {'error': '...'} döner.
    """
    if not isinstance(query, str) or not query.strip():
        return {"error": "query boş olmayan bir metin olmalı."}
    if not isinstance(ticker, str) or not ticker.strip():
        return {"error": "ticker boş olmayan bir metin olmalı (ör. 'AAPL')."}
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 10:
        return {"error": "k 1 ile 10 arasında bir tam sayı olmalı."}
    if year is not None and (isinstance(year, bool) or not isinstance(year, int)):
        return {"error": "year bir tam sayı olmalı (ör. 2024)."}

    sec = None
    if section is not None:
        sec = _normalize_section(section) if isinstance(section, str) else None
        if sec is None:
            return {"error": f"Geçersiz section {section!r}. Geçerli değerler: "
                             "item_1 (Business), item_1a (Risk Factors), item_7 (MD&A)."}

    symbol = ticker.strip().upper()
    try:
        chunks = load_chunks(symbol)
    except FileNotFoundError:
        return {"error": f"'{symbol}' için metin indeksi yok. "
                         f"Önce: python -m src.knowledge.index build {symbol}"}
    years = sorted({c["fy"] for c in chunks})
    if year is not None and year not in years:
        return {"error": f"FY{year} için indekslenmiş 10-K yok. Mevcut yıllar: {years}."}

    hits = BM25Index(chunks).search(query, k=k, ticker=symbol, section=sec, year=year)
    results = [
        {**h, "citation": f"{h['ticker']} {h['form']} FY{h['fy']}, "
                          f"{SECTION_LABELS[h['section']]}, accn {h['accn']}"}
        for h in hits
    ]
    out = {"query": query, "filters": {"ticker": symbol, "section": sec, "year": year},
           "retrieval": "bm25", "results": results}
    if not results:
        out["note"] = ("Sorguyla eşleşen metin bulunamadı. Farklı sözcüklerle dene ya da filtreyi "
                       "gevşet; yine bulunamazsa 'incelenen kaynaklarda bulunamadı' de.")
    return out


if __name__ == "__main__":
    import sys

    res = search_filings(sys.argv[2] if len(sys.argv) > 2 else "tariffs",
                         sys.argv[1] if len(sys.argv) > 1 else "AAPL",
                         section=sys.argv[3] if len(sys.argv) > 3 else None,
                         year=int(sys.argv[4]) if len(sys.argv) > 4 else None, k=3)
    if "error" in res:
        print("HATA:", res["error"])
    else:
        print(res["filters"], f"{len(res['results'])} sonuç")
        for r in res["results"]:
            print(f"\n[{r['score']}] {r['citation']}\n  " + r["text"][:200].replace("\n", " "))
        if "note" in res:
            print(res["note"])
