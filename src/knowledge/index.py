"""Chunk'lama ve BM25 arama indeksi (yalnızca standart kütüphane).

- Chunk'lar bölüm (Item 1 / 1A / 7) sınırını aşmaz; her chunk fiscal year, bölüm ve kaynak
  accn'ı taşır. Paragraf bütünlüğü korunur (overlap yok; paragraf zaten anlam birimidir).
- Sayfa altbilgileri ('Apple Inc. | 2024 Form 10-K | 26'), sayfa numaraları ve
  'Table of Contents' satırları chunk'lanmadan önce ayıklanır.
- Arama önce metadata filtresi (ticker / bölüm / yıl) uygular, sonra BM25 ile sıralar.
  Yıl filtresi şart: filtresiz arama '2023 geliri' sorusuna 2022 metnini getirebilir.
- Sayısal sorular bu indeksten değil XBRL tool'larından yanıtlanır.

CLI:
    python -m src.knowledge.index build AAPL
    python -m src.knowledge.index search AAPL "tariffs" --year 2025 --section item_1a -k 3
"""
import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECTIONS_DIR = ROOT / "data" / "cache" / "sections"
INDEX_DIR = ROOT / "data" / "cache" / "index"

MAX_CHARS = 1200
RESULT_KEYS = ("chunk_id", "ticker", "form", "fy", "section", "accn", "url", "text")

FOOTER = re.compile(r"^[^\n|]{1,60}\|\s*\d{4}\s+Form\s+10-K\s*\|\s*\d+$", re.IGNORECASE)
PAGE_NUMBER = re.compile(r"^\d{1,3}$")
STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with".split()
)


# ---------------------------------------------------------------- temizlik ve chunk'lama
def clean_page_lines(text: str) -> str:
    """Sayfa altbilgisi, tek başına sayfa numarası ve 'Table of Contents' satırlarını atar."""
    kept = []
    for line in text.split("\n"):
        s = line.strip()
        if FOOTER.match(s) or PAGE_NUMBER.match(s) or s.lower() == "table of contents":
            continue
        kept.append(line)
    return "\n".join(kept)


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    """max_chars'tan uzun tek paragrafı cümle sınırlarından böler."""
    out: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        while len(sentence) > max_chars:  # tek cümle bile uzunsa sert kes
            if current:
                out.append(current)
                current = ""
            out.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if current and len(current) + 1 + len(sentence) > max_chars:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        out.append(current)
    return out


def chunk_section(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Paragrafları max_chars'a kadar birleştirerek chunk'lar; paragrafı ortadan bölmez."""
    pieces: list[str] = []
    for line in text.split("\n"):
        p = line.strip()
        if not p:
            continue
        pieces.extend([p] if len(p) <= max_chars else _split_long(p, max_chars))
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def build_chunks(ticker: str) -> list[dict]:
    """data/cache/sections/<ticker>/*.json -> chunks.jsonl (data/cache/index/<ticker>/)."""
    chunks: list[dict] = []
    for path in sorted((SECTIONS_DIR / ticker).glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        for key, text in rec["sections"].items():
            for n, piece in enumerate(chunk_section(clean_page_lines(text))):
                chunks.append({
                    "chunk_id": f"{ticker}-FY{rec['fy']}-{key}-{n:03d}",
                    "ticker": ticker, "form": rec["form"], "fy": rec["fy"], "section": key,
                    "accn": rec["accn"], "url": rec["url"], "text": piece,
                })
    out_dir = INDEX_DIR / ticker
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return chunks


def load_chunks(ticker: str) -> list[dict]:
    path = INDEX_DIR / ticker / "chunks.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} yok. Önce: python -m src.knowledge.index build {ticker}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------- BM25
def _stem(token: str) -> str:
    """Çok hafif çoğul eki temizleyici ('tariffs' -> 'tariff', 'policies' -> 'policy')."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


class BM25Index:
    """Okapi BM25. IDF tüm chunk'lardan hesaplanır, filtre yalnızca adayları daraltır."""

    def __init__(self, chunks: list[dict], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.tfs = [Counter(tokenize(c["text"])) for c in chunks]
        self.lengths = [sum(tf.values()) for tf in self.tfs]
        self.avg_len = sum(self.lengths) / len(chunks) if chunks else 0.0
        df: Counter = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int = 5, ticker: str | None = None,
               section: str | None = None, year: int | None = None) -> list[dict]:
        terms = set(tokenize(query))
        if not terms:
            return []
        scored: list[tuple[float, int]] = []
        for i, chunk in enumerate(self.chunks):
            if ticker and chunk["ticker"].upper() != ticker.upper():
                continue
            if section and chunk["section"] != section:
                continue
            if year is not None and chunk["fy"] != year:
                continue
            tf, score = self.tfs[i], 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    norm = f + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg_len)
                    score += self.idf[t] * f * (self.k1 + 1) / norm
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [{**{key: self.chunks[i][key] for key in RESULT_KEYS}, "score": round(s, 4)}
                for s, i in scored[:k]]


# ---------------------------------------------------------------- CLI
def main() -> None:
    parser = argparse.ArgumentParser(description="10-K chunk indeksi")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_build = sub.add_parser("build", help="chunks.jsonl üret")
    p_build.add_argument("ticker")
    p_search = sub.add_parser("search", help="indekste ara")
    p_search.add_argument("ticker")
    p_search.add_argument("query")
    p_search.add_argument("--section", choices=["item_1", "item_1a", "item_7"])
    p_search.add_argument("--year", type=int)
    p_search.add_argument("-k", type=int, default=5)
    args = parser.parse_args()
    ticker = args.ticker.upper()

    if args.cmd == "build":
        chunks = build_chunks(ticker)
        by_section = Counter(c["section"] for c in chunks)
        by_year = Counter(c["fy"] for c in chunks)
        print(f"{ticker}: {len(chunks)} chunk")
        print("  bölüm başına:", dict(sorted(by_section.items())))
        print("  yıl başına  :", dict(sorted(by_year.items())))
        sizes = [len(c["text"]) for c in chunks]
        print(f"  chunk boyutu: ortalama {sum(sizes) // len(sizes)}, en küçük {min(sizes)}, en büyük {max(sizes)}")
    else:
        index = BM25Index(load_chunks(ticker))
        hits = index.search(args.query, k=args.k, ticker=ticker, section=args.section, year=args.year)
        print(f"{len(hits)} sonuç")
        for h in hits:
            print(f"\n[{h['score']}] FY{h['fy']} {h['section']} {h['accn']} {h['chunk_id']}")
            print("  " + h["text"][:240].replace("\n", " "))


if __name__ == "__main__":
    main()