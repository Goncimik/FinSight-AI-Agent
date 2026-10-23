"""compute_metrics birim testleri. Gerçek veri gerekmez."""
import json
import math

from src.tools import company_facts, compute_metrics as cm

CIK = 222
PERIOD = {"min_days": 350, "max_days": 380}


def row(end, value, accn="a", unit="USD"):
    return {"fiscal_year": int(end[:4]), "end": end, "value": value, "unit": unit, "accn": accn}


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-9)


def spec(name):
    return cm.load_metrics()["metrics"][name]


# ---- cagr() saf fonksiyonu ----
def test_cagr_known_value():
    assert close(cm.cagr(100, 121, 2), 0.10)


def test_cagr_rejects_invalid_inputs():
    for args in [(0, 10, 2), (-5, 10, 2), (100, 121, 0), (100, -1, 2)]:
        try:
            cm.cagr(*args)
        except ValueError:
            continue
        raise AssertionError(f"ValueError beklenirdi: {args}")


# ---- marj ----
def test_margin_value_inputs_and_sources():
    rows = {"operating_income": [row("2024-09-28", 30, "acc-op")],
            "revenue": [row("2024-09-28", 120, "acc-rev")]}
    out = cm.compute_metric_from_rows("operating_margin", spec("operating_margin"), rows, PERIOD)
    r = out["results"][0]
    assert close(r["value"], 0.25)
    assert r["inputs"] == {"operating_income": 30, "revenue": 120}
    assert r["source_accns"] == ["acc-op", "acc-rev"]
    assert out["formula"] == "operating_income / revenue"


def test_margin_skips_period_with_missing_input_and_says_why():
    rows = {"net_income": [row("2023-09-30", 10)],
            "revenue": [row("2023-09-30", 100), row("2024-09-28", 200)]}
    out = cm.compute_metric_from_rows("net_margin", spec("net_margin"), rows, PERIOD)
    assert [r["end"] for r in out["results"]] == ["2023-09-30"]
    assert out["skipped"] == [{"end": "2024-09-28", "reason": "net_income verisi yok"}]


def test_margin_zero_denominator_is_skipped():
    rows = {"net_income": [row("2023-09-30", 10)], "revenue": [row("2023-09-30", 0)]}
    out = cm.compute_metric_from_rows("net_margin", spec("net_margin"), rows, PERIOD)
    assert "error" in out and "sıfır" in out["skipped"][0]["reason"]


# ---- büyüme ----
def test_growth_values_for_consecutive_years():
    rows = {"revenue": [row("2022-09-24", 100), row("2023-09-30", 110), row("2024-09-28", 121)]}
    out = cm.compute_metric_from_rows("revenue_growth", spec("revenue_growth"), rows, PERIOD)
    assert [r["end"] for r in out["results"]] == ["2023-09-30", "2024-09-28"]
    assert close(out["results"][0]["value"], 0.10) and close(out["results"][1]["value"], 0.10)


def test_growth_skips_gap_between_non_consecutive_periods():
    rows = {"revenue": [row("2021-09-25", 100), row("2023-09-30", 130)]}  # 2022 yok
    out = cm.compute_metric_from_rows("revenue_growth", spec("revenue_growth"), rows, PERIOD)
    assert "error" in out and "ardışık değil" in out["skipped"][0]["reason"]


# ---- CAGR metriği ----
def test_cagr_metric_uses_number_of_periods_not_rows():
    rows = {"revenue": [row("2021-09-25", 100), row("2022-09-24", 110), row("2023-09-30", 121)]}
    out = cm.compute_metric_from_rows("revenue_cagr", spec("revenue_cagr"), rows, PERIOD)
    r = out["results"][0]
    assert r["inputs"]["periods"] == 2 and close(r["value"], 0.10)


def test_cagr_metric_errors_on_gap_and_on_single_row():
    gap = {"revenue": [row("2021-09-25", 100), row("2023-09-30", 121)]}
    assert "veri boşluğu" in cm.compute_metric_from_rows("revenue_cagr", spec("revenue_cagr"), gap, PERIOD)["error"]
    one = {"revenue": [row("2021-09-25", 100)]}
    assert "en az 2" in cm.compute_metric_from_rows("revenue_cagr", spec("revenue_cagr"), one, PERIOD)["error"]


# ---- birim kontrolü ----
def test_unit_mismatch_returns_error():
    rows = {"operating_income": [row("2024-09-28", 30, unit="EUR")],
            "revenue": [row("2024-09-28", 120)]}
    out = cm.compute_metric_from_rows("operating_margin", spec("operating_margin"), rows, PERIOD)
    assert "Birim uyuşmazlığı" in out["error"]


# ---- uçtan uca: compute_metrics (get_company_facts dahil) ----
def build_company(tmp_path, monkeypatch, facts):
    company = tmp_path / "TEST"
    company.mkdir()
    (company / "manifest.json").write_text(json.dumps({"ticker": "TEST", "cik": CIK}))
    body = {tag: {"units": {"USD": rows}} for tag, rows in facts.items()}
    (company / "companyfacts.json").write_text(json.dumps({"facts": {"us-gaap": body}}))
    monkeypatch.setattr(company_facts, "DATA_DIR", tmp_path)


def fact(end, val, accn):
    y = int(end[:4])
    return {"start": f"{y - 1}-10-01", "end": end, "val": val, "fy": y, "fp": "FY",
            "form": "10-K", "filed": f"{y}-11-01", "accn": accn}


def test_compute_metrics_end_to_end(tmp_path, monkeypatch):
    build_company(tmp_path, monkeypatch, {
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            fact("2023-09-30", 100, "r23"), fact("2024-09-28", 120, "r24")],
        "OperatingIncomeLoss": [fact("2023-09-30", 20, "o23"), fact("2024-09-28", 30, "o24")],
    })
    out = cm.compute_metrics(CIK, "operating_margin", years=2)
    assert [round(r["value"], 4) for r in out["results"]] == [0.2, 0.25]
    assert out["results"][1]["source_accns"] == ["o24", "r24"] and out["cik"] == CIK
    growth = cm.compute_metrics(CIK, "revenue_growth", years=2)
    assert close(growth["results"][0]["value"], 0.20)


def test_compute_metrics_error_cases(tmp_path, monkeypatch):
    build_company(tmp_path, monkeypatch, {"NetIncomeLoss": []})
    assert "Bilinmeyen metric" in cm.compute_metrics(CIK, "foo")["error"]
    assert "years" in cm.compute_metrics(CIK, "revenue_cagr", years=1)["error"]
    assert "verisi alınamadı" in cm.compute_metrics(CIK, "revenue_cagr")["error"]
    assert "verisi alınamadı" in cm.compute_metrics(999, "net_margin")["error"]