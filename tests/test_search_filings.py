"""search_filings birim testleri. Gerçek indeks yerine küçük sahte chunk dosyası kullanılır."""
import json

from src.knowledge import index as idx
from src.tools import search_filings as sf


def chunk(text, fy, section, n):
    return {"chunk_id": f"TEST-FY{fy}-{section}-{n:03d}", "ticker": "TEST", "form": "10-K", "fy": fy,
            "section": section, "accn": f"acc-{fy}", "url": f"http://x/{fy}", "text": text}


CHUNKS = [
    chunk("new tariffs were announced on imports and affect component costs", 2025, "item_1a", 0),
    chunk("tariffs on imports could raise costs for the Company", 2024, "item_1a", 1),
    chunk("net sales increased due to Services growth and tariffs relief", 2024, "item_7", 2),
    chunk("The Company designs and sells smartphones and accessories", 2024, "item_1", 3),
]


def use_index(tmp_path, monkeypatch, chunks=CHUNKS):
    folder = tmp_path / "TEST"
    folder.mkdir()
    (folder / "chunks.jsonl").write_text("\n".join(json.dumps(c) for c in chunks) + "\n")
    monkeypatch.setattr(idx, "INDEX_DIR", tmp_path)


def test_results_carry_citation_fields(tmp_path, monkeypatch):
    use_index(tmp_path, monkeypatch)
    out = sf.search_filings("tariffs", "TEST", year=2025)
    r = out["results"][0]
    assert {"text", "ticker", "form", "fy", "section", "accn", "url", "chunk_id", "score",
            "citation"} <= set(r)
    assert r["citation"] == "TEST 10-K FY2025, Item 1A (Risk Factors), accn acc-2025"
    assert out["retrieval"] == "bm25" and out["filters"] == {"ticker": "TEST", "section": None, "year": 2025}


def test_year_and_section_filters_are_applied(tmp_path, monkeypatch):
    use_index(tmp_path, monkeypatch)
    assert {r["fy"] for r in sf.search_filings("tariffs", "TEST", year=2024)["results"]} == {2024}
    only_mdna = sf.search_filings("tariffs", "TEST", section="item_7")["results"]
    assert [r["section"] for r in only_mdna] == ["item_7"]


def test_section_aliases_are_understood(tmp_path, monkeypatch):
    use_index(tmp_path, monkeypatch)
    for alias in ["Item 1A", "item_1a", "risk factors", "1A", "Item 1A."]:
        res = sf.search_filings("tariffs", "TEST", section=alias)["results"]
        assert res and {r["section"] for r in res} == {"item_1a"}, alias
    assert {r["section"] for r in sf.search_filings("tariffs", "TEST", section="MD&A")["results"]} == {"item_7"}


def test_ticker_is_case_insensitive_and_k_limits_results(tmp_path, monkeypatch):
    use_index(tmp_path, monkeypatch)
    assert sf.search_filings("tariffs", "test", k=1)["filters"]["ticker"] == "TEST"
    assert len(sf.search_filings("tariffs", "test", k=1)["results"]) == 1


def test_unknown_year_lists_available_years(tmp_path, monkeypatch):
    use_index(tmp_path, monkeypatch)
    err = sf.search_filings("tariffs", "TEST", year=2019)["error"]
    assert "FY2019" in err and "[2024, 2025]" in err


def test_missing_index_tells_how_to_build_it(tmp_path, monkeypatch):
    monkeypatch.setattr(idx, "INDEX_DIR", tmp_path)
    err = sf.search_filings("tariffs", "NOPE")["error"]
    assert "NOPE" in err and "build" in err


def test_invalid_arguments_return_errors_not_exceptions(tmp_path, monkeypatch):
    use_index(tmp_path, monkeypatch)
    assert "query" in sf.search_filings("   ", "TEST")["error"]
    assert "ticker" in sf.search_filings("tariffs", "")["error"]
    assert "k" in sf.search_filings("tariffs", "TEST", k=0)["error"]
    assert "k" in sf.search_filings("tariffs", "TEST", k=11)["error"]
    assert "k" in sf.search_filings("tariffs", "TEST", k=True)["error"]
    assert "year" in sf.search_filings("tariffs", "TEST", year="2024")["error"]
    assert "Geçersiz section" in sf.search_filings("tariffs", "TEST", section="item_9")["error"]


def test_no_match_returns_empty_results_with_honest_note(tmp_path, monkeypatch):
    use_index(tmp_path, monkeypatch)
    out = sf.search_filings("zzzzqqq", "TEST")
    assert out["results"] == [] and "bulunamadı" in out["note"]
