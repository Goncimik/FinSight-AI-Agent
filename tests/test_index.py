"""index.py birim testleri: temizlik, chunk'lama, BM25 ve metadata filtresi."""
import json

from src.knowledge import index as idx


def make_chunk(text, fy=2024, section="item_1a", n=0):
    return {"chunk_id": f"T-FY{fy}-{section}-{n:03d}", "ticker": "TEST", "form": "10-K", "fy": fy,
            "section": section, "accn": f"acc-{fy}", "url": "http://x", "text": text}


# ---- temizlik ----
def test_clean_page_lines_removes_footer_page_numbers_and_toc_marker():
    text = ("Gerçek paragraf.\nApple Inc. | 2024 Form 10-K | 26\n27\nTable of Contents\n"
            "Revenue was 391 billion.\nTable of Contents and more words")
    cleaned = idx.clean_page_lines(text)
    assert "Form 10-K" not in cleaned and "\n27\n" not in f"\n{cleaned}\n"
    assert "Gerçek paragraf." in cleaned and "Revenue was 391 billion." in cleaned
    assert "Table of Contents and more words" in cleaned  # yalnızca tek başına duran satır atılır


# ---- chunk'lama ----
def test_chunks_respect_max_size_and_lose_no_paragraph():
    paragraphs = [f"Paragraf {i}. " + "kelime " * 40 for i in range(30)]
    chunks = idx.chunk_section("\n".join(paragraphs), max_chars=500)
    assert all(len(c) <= 500 for c in chunks) and len(chunks) > 1
    joined = "\n".join(chunks)
    assert all(p.strip() in joined for p in paragraphs)


def test_paragraphs_are_not_split_when_they_fit():
    paragraphs = ["A " * 100, "B " * 100, "C " * 100]
    chunks = idx.chunk_section("\n".join(paragraphs), max_chars=450)
    for p in paragraphs:
        assert any(p.strip() in c for c in chunks)


def test_oversize_paragraph_is_split_at_sentence_boundaries():
    para = " ".join(f"Cümle numara {i} burada biter." for i in range(60))
    chunks = idx.chunk_section(para, max_chars=300)
    assert len(chunks) > 1 and all(len(c) <= 300 for c in chunks)
    assert all(c.rstrip().endswith(".") for c in chunks)


def test_single_giant_sentence_is_hard_cut():
    chunks = idx.chunk_section("x" * 1000, max_chars=300)
    assert [len(c) for c in chunks] == [300, 300, 300, 100]


def test_empty_text_gives_no_chunks():
    assert idx.chunk_section("") == [] and idx.chunk_section("\n \n") == []


# ---- BM25 ----
def test_relevant_chunk_ranks_first_and_stemming_matches_plurals():
    chunks = [make_chunk("The Company faces tariff and trade restrictions in several markets.", n=0),
              make_chunk("Our retail stores sell accessories and music subscriptions.", n=1),
              make_chunk("Foreign currency exchange rates affect revenue.", n=2)]
    hits = idx.BM25Index(chunks).search("tariffs", k=3)
    assert [h["chunk_id"] for h in hits] == ["T-FY2024-item_1a-000"]


def test_query_without_matching_or_only_stopwords_returns_empty():
    index = idx.BM25Index([make_chunk("supply chain disruption")])
    assert index.search("zzzzqqq") == []
    assert index.search("the of and") == []
    assert index.search("") == []


def test_year_and_section_filters_prevent_wrong_year_results():
    chunks = [make_chunk("net sales increased due to iPhone", fy=2022, section="item_7", n=0),
              make_chunk("net sales increased due to iPhone", fy=2023, section="item_7", n=1),
              make_chunk("net sales increased due to iPhone", fy=2023, section="item_1a", n=2)]
    index = idx.BM25Index(chunks)
    assert len(index.search("net sales iPhone", k=10)) == 3
    only_2023 = index.search("net sales iPhone", k=10, year=2023)
    assert {h["fy"] for h in only_2023} == {2023}
    both = index.search("net sales iPhone", k=10, year=2023, section="item_7")
    assert [h["chunk_id"] for h in both] == ["T-FY2023-item_7-001"]


def test_k_limits_results_and_ranking_is_deterministic():
    chunks = [make_chunk(f"tariff risk {i}", n=i) for i in range(8)]
    index = idx.BM25Index(chunks)
    first = index.search("tariff", k=3)
    assert len(first) == 3 and first == index.search("tariff", k=3)


def test_result_carries_citation_metadata_and_score():
    hit = idx.BM25Index([make_chunk("component shortage risk")]).search("shortage")[0]
    assert {"chunk_id", "ticker", "form", "fy", "section", "accn", "url", "text", "score"} <= set(hit)
    assert hit["accn"] == "acc-2024" and hit["score"] > 0


# ---- disk: build_chunks / load_chunks ----
def test_build_and_load_chunks_roundtrip(tmp_path, monkeypatch):
    sections_dir, index_dir = tmp_path / "sections", tmp_path / "index"
    (sections_dir / "TEST").mkdir(parents=True)
    rec = {"ticker": "TEST", "form": "10-K", "fy": 2024, "accn": "0000111111-24-000001",
           "url": "http://x/doc.htm",
           "sections": {"item_1": "Business text.\nApple Inc. | 2024 Form 10-K | 3\nMore business text.",
                        "item_7": "MD&A text."}}
    (sections_dir / "TEST" / "2024-09-28_x.json").write_text(json.dumps(rec))
    monkeypatch.setattr(idx, "SECTIONS_DIR", sections_dir)
    monkeypatch.setattr(idx, "INDEX_DIR", index_dir)

    built = idx.build_chunks("TEST")
    loaded = idx.load_chunks("TEST")
    assert built == loaded and len(built) == 2
    assert built[0]["chunk_id"] == "TEST-FY2024-item_1-000"
    assert "Form 10-K" not in built[0]["text"]
    assert {c["accn"] for c in built} == {"0000111111-24-000001"}


def test_load_chunks_without_build_explains_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(idx, "INDEX_DIR", tmp_path)
    try:
        idx.load_chunks("TEST")
    except FileNotFoundError as exc:
        assert "build" in str(exc)
    else:
        raise AssertionError("FileNotFoundError beklenirdi")
