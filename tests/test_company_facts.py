"""get_company_facts birim testleri. Gerçek veri gerekmez; küçük sentetik veri kurulur."""
import json

from src.tools import company_facts

CIK = 111
NEW = "RevenueFromContractWithCustomerExcludingAssessedTax"
OLD = "SalesRevenueNet"


def rec(start, end, val, fy, form, filed, accn):
    return {"start": start, "end": end, "val": val, "fy": fy, "fp": "FY",
            "form": form, "filed": filed, "accn": accn}


def build(tmp_path, monkeypatch, facts):
    """tmp_path altında sahte bir şirket klasörü kurar ve modülü ona yönlendirir."""
    company = tmp_path / "TEST"
    company.mkdir()
    (company / "manifest.json").write_text(json.dumps({"ticker": "TEST", "cik": CIK}))
    body = {tag: {"units": {"USD": rows}} for tag, rows in facts.items()}
    (company / "companyfacts.json").write_text(json.dumps({"facts": {"us-gaap": body}}))
    monkeypatch.setattr(company_facts, "DATA_DIR", tmp_path)


def test_tag_fallback_merges_series_per_period(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, {
        OLD: [rec("2016-09-25", "2017-09-30", 100, 2017, "10-K", "2017-11-01", "a17")],
        NEW: [rec("2017-10-01", "2018-09-29", 200, 2018, "10-K", "2018-11-01", "a18")],
    })
    out = company_facts.get_company_facts(CIK, "revenue", years=5)
    assert [(r["end"], r["tag"]) for r in out["rows"]] == [
        ("2017-09-30", OLD), ("2018-09-29", NEW)]


def test_higher_priority_tag_wins_on_overlapping_period(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, {
        OLD: [rec("2016-09-25", "2017-09-30", 100, 2017, "10-K", "2017-11-01", "a17")],
        NEW: [rec("2016-09-25", "2017-09-30", 101, 2019, "10-K", "2019-11-01", "a19")],
    })
    out = company_facts.get_company_facts(CIK, "revenue")
    assert len(out["rows"]) == 1
    assert out["rows"][0]["tag"] == NEW and out["rows"][0]["value"] == 101


def test_same_period_reported_in_three_filings_gives_one_row(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, {NEW: [
        rec("2022-09-25", "2023-09-30", 383, 2023, "10-K", "2023-11-03", "a23"),
        rec("2022-09-25", "2023-09-30", 383, 2024, "10-K", "2024-11-01", "a24"),
        rec("2022-09-25", "2023-09-30", 383, 2025, "10-K", "2025-10-31", "a25"),
    ]})
    out = company_facts.get_company_facts(CIK, "revenue")
    assert len(out["rows"]) == 1
    assert out["rows"][0]["fiscal_year"] == 2023  # fy (2025) değil, dönem sonu yılı


def test_latest_filed_wins_when_values_differ(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, {NEW: [
        rec("2022-09-25", "2023-09-30", 380, 2023, "10-K", "2023-11-03", "orig"),
        rec("2022-09-25", "2023-09-30", 383, 2023, "10-K/A", "2024-02-01", "amend"),
    ]})
    row = company_facts.get_company_facts(CIK, "revenue")["rows"][0]
    assert row["value"] == 383 and row["accn"] == "amend" and row["form"] == "10-K/A"


def test_quarterly_and_10q_records_are_excluded(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, {NEW: [
        rec("2023-10-01", "2023-12-30", 90, 2024, "10-Q", "2024-02-02", "q1"),
        rec("2023-10-01", "2023-12-30", 90, 2024, "10-K", "2024-11-01", "quarter-in-10k"),
        rec("2022-09-25", "2023-09-30", 383, 2023, "10-K", "2023-11-03", "a23"),
    ]})
    rows = company_facts.get_company_facts(CIK, "revenue")["rows"]
    assert [r["accn"] for r in rows] == ["a23"]


def test_53_week_year_is_accepted(tmp_path, monkeypatch):
    # 2022-09-25 -> 2023-09-30 = 370 gün
    build(tmp_path, monkeypatch, {NEW: [
        rec("2022-09-25", "2023-09-30", 383, 2023, "10-K", "2023-11-03", "a23")]})
    assert len(company_facts.get_company_facts(CIK, "revenue")["rows"]) == 1


def test_years_limit_returns_latest_ascending(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, {NEW: [
        rec(f"{y - 1}-10-01", f"{y}-09-30", y, y, "10-K", f"{y}-11-01", f"a{y}")
        for y in range(2019, 2026)]})
    out = company_facts.get_company_facts(CIK, "revenue", years=3)
    assert [r["fiscal_year"] for r in out["rows"]] == [2023, 2024, 2025]


def test_fewer_years_than_requested_adds_note(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, {NEW: [
        rec("2022-09-25", "2023-09-30", 383, 2023, "10-K", "2023-11-03", "a23")]})
    out = company_facts.get_company_facts(CIK, "revenue", years=5)
    assert len(out["rows"]) == 1 and "note" in out


def test_error_messages(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, {NEW: []})
    assert "Bilinmeyen concept" in company_facts.get_company_facts(CIK, "foo")["error"]
    assert "years" in company_facts.get_company_facts(CIK, "revenue", years=0)["error"]
    assert "CIK 999" in company_facts.get_company_facts(999, "revenue")["error"]
    assert "bulunamadı" in company_facts.get_company_facts(CIK, "revenue")["error"]