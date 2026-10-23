"""resolve_ticker_to_cik birim testleri. SEC dosyası yerine küçük sahte bir liste kullanılır."""
import json

from src.tools import resolve_ticker

FAKE = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


def use_fake_list(tmp_path, monkeypatch):
    path = tmp_path / "company_tickers.json"
    path.write_text(json.dumps(FAKE))
    monkeypatch.setattr(resolve_ticker, "TICKERS_PATH", path)


def test_resolves_known_ticker_ignoring_case_and_spaces(tmp_path, monkeypatch):
    use_fake_list(tmp_path, monkeypatch)
    expected = {"ticker": "AAPL", "cik": 320193, "name": "Apple Inc."}
    assert resolve_ticker.resolve_ticker_to_cik("AAPL") == expected
    assert resolve_ticker.resolve_ticker_to_cik("  aapl ") == expected


def test_cik_is_an_integer(tmp_path, monkeypatch):
    use_fake_list(tmp_path, monkeypatch)
    assert isinstance(resolve_ticker.resolve_ticker_to_cik("MSFT")["cik"], int)


def test_unknown_ticker_returns_helpful_error(tmp_path, monkeypatch):
    use_fake_list(tmp_path, monkeypatch)
    err = resolve_ticker.resolve_ticker_to_cik("ZZZZ")["error"]
    assert "ZZZZ" in err and "bulunamadı" in err


def test_empty_or_non_string_ticker_returns_error(tmp_path, monkeypatch):
    use_fake_list(tmp_path, monkeypatch)
    for bad in ["", "   ", None, 123]:
        assert "error" in resolve_ticker.resolve_ticker_to_cik(bad)


def test_missing_ticker_file_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(resolve_ticker, "TICKERS_PATH", tmp_path / "yok.json")
    assert "download_edgar" in resolve_ticker.resolve_ticker_to_cik("AAPL")["error"]