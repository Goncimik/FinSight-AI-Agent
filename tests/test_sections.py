"""sections.py birim testleri. Gerçek 10-K gerekmez; kısa sentetik belgeler kurulur."""
import json

from src.knowledge import sections as sec

FILLER = "Lorem ipsum dolor sit amet. " * 150  # yaklaşık 4200 karakter


def make_doc(toc: str, body_extra: str = "") -> str:
    return (
        toc
        + "Item 1. Business\nBODY1 " + FILLER + body_extra + "\n"
        + "Item 1A. Risk Factors\nBODY1A " + FILLER + "\n"
        + "Item 1B. Unresolved Staff Comments\nNone.\n"
        + "Item 7. Management’s Discussion and Analysis of Financial Condition\nBODY7 " + FILLER + "\n"
        + "Item 7A. Quantitative and Qualitative Disclosures About Market Risk\nText.\n"
    )


TOC_FULL = ("PART I\nItem 1. Business 1\nItem 1A. Risk Factors 5\nItem 1B. Unresolved Staff Comments 17\n"
            "Item 7. Management’s Discussion and Analysis 20\n"
            "Item 7A. Quantitative and Qualitative Disclosures 30\n")


def test_html_to_text_removes_hidden_ixbrl_script_style_and_unescapes():
    raw = ("<html><style>.a{color:red}</style><ix:header><ix:hidden>SECRET 12345</ix:hidden></ix:header>"
           "<script>var x=1;</script><div>Item&#160;1. Business</div><p>Tom &amp; Jerry<br/>line2</p></html>")
    text = sec.html_to_text(raw)
    assert "SECRET" not in text and "color" not in text and "var x" not in text
    assert "Item 1. Business" in text and "Tom & Jerry" in text
    assert text.split("\n")[-1] == "line2"


def test_picks_real_section_over_table_of_contents():
    sections, failures = sec.extract_sections(make_doc(TOC_FULL))
    assert not failures
    assert "BODY1 " in sections["item_1"] and "BODY1A " in sections["item_1a"]
    assert "BODY7 " in sections["item_7"]
    assert "Item 1B" not in sections["item_1a"] and "Item 7A" not in sections["item_7"]


def test_toc_without_following_items_does_not_swallow_the_body():
    toc = "Item 1. Business 1\nItem 1A. Risk Factors 5\nItem 7. Management’s Discussion 20\n"
    sections, failures = sec.extract_sections(make_doc(toc))
    assert not failures
    assert sections["item_1a"].startswith("Item 1A. Risk Factors\nBODY1A")
    assert "BODY1 " not in sections["item_1a"]


def test_mid_sentence_cross_reference_is_not_a_boundary():
    extra = "\nRisks are described in Part I, Item 1A. Risk Factors of this Form 10-K and Item 7. MD&A."
    sections, _ = sec.extract_sections(make_doc(TOC_FULL, body_extra=extra))
    assert "of this Form 10-K and Item 7. MD&A." in sections["item_1"]


def test_uppercase_and_split_headings_are_recognised():
    doc = ("Item 1. Business\n" + FILLER + "\nITEM 1A.\nRISK FACTORS\n" + FILLER +
           "\nItem 1B. Unresolved Staff Comments\nNone.\nITEM 7. MANAGEMENT’S DISCUSSION\n" + FILLER +
           "\nItem 7A. Quantitative\nx\n")
    sections, failures = sec.extract_sections(doc)
    assert not failures and set(sections) == {"item_1", "item_1a", "item_7"}


def test_missing_section_is_reported_but_others_survive():
    doc = ("Item 1. Business\n" + FILLER + "\nItem 1A. Risk Factors\n" + FILLER +
           "\nItem 1B. Unresolved Staff Comments\nNone.\n")
    sections, failures = sec.extract_sections(doc)
    assert set(sections) == {"item_1", "item_1a"}
    assert "bulunamadı" in failures["item_7"]


def test_too_short_section_is_a_failure():
    doc = ("Item 1. Business\n" + FILLER + "\nItem 1A. Risk Factors\nkısa\nItem 1B. Unresolved\nNone.\n"
           "Item 7. Management’s Discussion\n" + FILLER + "\nItem 7A. Quantitative\nx\n")
    sections, failures = sec.extract_sections(doc)
    assert "item_1a" not in sections and "karakter" in failures["item_1a"]


def test_process_ticker_writes_json_with_metadata(tmp_path, monkeypatch):
    raw_dir, out_dir = tmp_path / "raw", tmp_path / "out"
    (raw_dir / "TEST" / "10-K").mkdir(parents=True)
    html_path = raw_dir / "TEST" / "10-K" / "doc.htm"
    html_path.write_text("<div>" + make_doc(TOC_FULL).replace("\n", "</div><div>") + "</div>")
    manifest = {"ticker": "TEST", "cik": 111, "10-K": [{
        "form": "10-K", "accession": "0000111111-24-000001", "document": "doc.htm",
        "filing_date": "2024-11-01", "report_date": "2024-09-28", "file": str(html_path)}]}
    (raw_dir / "TEST" / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(sec, "RAW_DIR", raw_dir)
    monkeypatch.setattr(sec, "OUT_DIR", out_dir)

    report = sec.process_ticker("TEST")
    assert report[0]["failures"] == {} and report[0]["fy"] == 2024
    saved = json.loads((out_dir / "TEST" / "2024-09-28_000011111124000001.json").read_text())
    assert saved["accn"] == "0000111111-24-000001" and saved["form"] == "10-K"
    assert saved["url"] == "https://www.sec.gov/Archives/edgar/data/111/000011111124000001/doc.htm"
    assert set(saved["sections"]) == {"item_1", "item_1a", "item_7"}