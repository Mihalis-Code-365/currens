"""Issue #20: every way a rate reaches the cache, or is read back from it, is checked."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from currens import RateProviderUnavailableError
from currens.apis import rate_sources
from currens.collector import core
from currens.db import session as db_session
from currens.db.models import Base
from currens.service import (
    _normalize_provider_rows,
    configure_service_database,
    get_rate,
    get_rates_for_period,
    has_rate,
)

DAY = date(2025, 4, 16)
NEXT_DAY = date(2025, 4, 17)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.status = 200
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        return None


def _respond(monkeypatch, payload) -> list[str]:
    """Serve `payload` as every provider answer; returns the URLs asked for."""
    urls: list[str] = []

    def fake_urlopen(url, timeout=None):
        urls.append(url)
        return _FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(rate_sources, "urlopen", fake_urlopen)
    return urls


def _no_network(monkeypatch) -> None:
    def fake_urlopen(url, timeout=None):
        raise AssertionError(f"unexpected provider call: {url}")

    monkeypatch.setattr(rate_sources, "urlopen", fake_urlopen)


def _ecb_series(values: list[float], periods: list[str]) -> dict:
    return {
        "dataSets": [
            {
                "series": {
                    "0:0:0:0:0": {
                        "observations": {str(i): [v] for i, v in enumerate(values)}
                    }
                }
            }
        ],
        "structure": {
            "dimensions": {"observation": [{"values": [{"id": p} for p in periods]}]}
        },
    }


def _seed_cache(db_path: Path, rows: list[tuple[str, str, int, int, float]]) -> None:
    configure_service_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            "INSERT INTO exchange_rates (source, exchange_rate_date, base_currency_id,"
            " target_currency_id, value) VALUES (?, ?, ?, ?, ?)",
            rows,
        )


# --- 1. duplicate dates in a provider answer ---------------------------------------------


@pytest.mark.parametrize(
    ("pair", "payload"),
    [
        (
            ("USD", "SEK"),
            [{"date": "2025-04-16", "value": 10}, {"date": "2025-04-16", "value": 5}],
        ),
        (("USD", "EUR"), _ecb_series([1.25, 2.0], ["2025-04-16", "2025-04-16"])),
    ],
    ids=["riksbank", "ecb"],
)
def test_conflicting_rates_for_one_date_raise_unavailable(
    monkeypatch, tmp_path, pair, payload
) -> None:
    _respond(monkeypatch, payload)

    with pytest.raises(RateProviderUnavailableError, match="2025-04-16"):
        get_rate(DAY, *pair, db_path=tmp_path / "currens.db")


def test_repeated_identical_row_is_accepted(monkeypatch, tmp_path) -> None:
    row = {"date": "2025-04-16", "value": 10}
    _respond(monkeypatch, [row, row])

    assert get_rate(DAY, "USD", "SEK", db_path=tmp_path / "currens.db") == Decimal("10")


# --- 2. dates absent from the cache --------------------------------------------------------


def test_date_absent_from_the_cache_is_fetched_from_the_provider(monkeypatch, tmp_path) -> None:
    currens_db = tmp_path / "currens.db"
    _seed_cache(currens_db, [("Riksbank", "2025-04-16", 2, 3, 10.0)])

    urls = _respond(monkeypatch, [{"date": "2025-04-17", "value": 10}])

    # The cached date is served without a fetch; only the absent one goes to the provider.
    assert get_rate(DAY, "USD", "SEK", db_path=currens_db) == Decimal("10.0")
    assert urls == []
    assert get_rate(NEXT_DAY, "SEK", "USD", db_path=currens_db) == Decimal("0.1")
    assert len(urls) == 1


# --- 3. rows already in the cache ---------------------------------------------------------


@pytest.mark.parametrize("bad_value", [0.0, -10.0, float("inf")], ids=["zero", "negative", "infinity"])
@pytest.mark.parametrize(
    "pair", [("USD", "SEK"), ("SEK", "USD")], ids=["direct", "inverse"]
)
def test_bad_cached_rate_is_treated_as_missing(monkeypatch, tmp_path, bad_value, pair) -> None:
    currens_db = tmp_path / "currens.db"
    # Source "legacy" so the Riksbank migration does not delete it first.
    _seed_cache(currens_db, [("legacy", "2025-04-16", 2, 3, bad_value)])
    urls = _respond(monkeypatch, [{"date": "2025-04-16", "value": 10}])

    expected = Decimal("10") if pair == ("USD", "SEK") else Decimal("0.1")
    assert get_rate(DAY, *pair, db_path=currens_db) == expected
    assert len(urls) == 1


def test_bad_cached_rate_does_not_count_as_coverage(monkeypatch, tmp_path) -> None:
    currens_db = tmp_path / "currens.db"
    _seed_cache(currens_db, [("legacy", "2025-04-16", 2, 3, 0.0)])
    _no_network(monkeypatch)

    assert has_rate(DAY, "SEK", "USD", db_path=currens_db) is False


# --- 4. the legacy collector --------------------------------------------------------------


@pytest.fixture
def collector_db(monkeypatch, tmp_path):
    currens_db = tmp_path / "currens.db"
    db_session.configure_database(db_path=currens_db)
    Base.metadata.create_all(bind=db_session.engine)
    yield currens_db
    db_session.configure_database()


@pytest.mark.parametrize(
    "rows",
    [
        [{"date": "2025-04-16", "value": 0}],
        [{"date": "2025-04-16", "value": -10}],
        {"unexpected": "object"},
        [{"date": "2025-04-16", "value": 10}, {"date": "2025-04-16", "value": 5}],
    ],
    ids=["zero", "negative", "not-a-list", "conflicting-duplicate"],
)
def test_collector_stores_nothing_from_a_bad_riksbank_answer(
    monkeypatch, collector_db, capsys, rows
) -> None:
    monkeypatch.setattr(
        core, "get_exchange_rates_from_riksbank", lambda *args, **kwargs: rows
    )

    core.store_riksbank_rates(currency_id=2, start_date="2025-04-16")

    assert "Error" in capsys.readouterr().out
    assert has_rate(DAY, "USD", "SEK", db_path=collector_db) is False


def test_collector_stores_nothing_from_a_bad_ecb_answer(
    monkeypatch, collector_db, capsys
) -> None:
    monkeypatch.setattr(
        core,
        "get_exchange_rates_from_european_central_bank",
        lambda *args, **kwargs: [{"date": "2025-04-16", "value": 0}],
    )

    core.store_european_central_bank_rates(1, 2, start_date="2025-04-16")

    assert "Error" in capsys.readouterr().out
    assert has_rate(DAY, "EUR", "USD", db_path=collector_db) is False


# --- review follow-ups on #21 -------------------------------------------------------------


def test_rates_for_period_returns_one_usable_rate_per_date(monkeypatch, tmp_path) -> None:
    currens_db = tmp_path / "currens.db"
    _seed_cache(currens_db, [("legacy", "2025-04-16", 2, 3, 0.0)])
    _respond(monkeypatch, [{"date": "2025-04-16", "value": 10}])

    records = get_rates_for_period("USD", "SEK", DAY, DAY, db_path=currens_db)

    assert [(r.exchange_rate_date, r.rate) for r in records] == [(DAY, Decimal("10.0"))]


def test_signaling_nan_in_data_envelope_raises_unavailable() -> None:
    with pytest.raises(RateProviderUnavailableError):
        _normalize_provider_rows(
            rows={"data": [{"date": "2025-04-16", "value": "sNaN"}]},
            source="Riksbank",
            base_currency_id=2,
            rate_currency_id=3,
            invert_rate=False,
        )


def test_rate_too_small_to_store_raises_unavailable(monkeypatch, tmp_path) -> None:
    # A positive Decimal that becomes 0.0 as the cache's REAL would be served as missing.
    _respond(monkeypatch, [{"date": "2025-04-16", "value": "1e-400"}])

    with pytest.raises(RateProviderUnavailableError):
        get_rate(DAY, "USD", "SEK", db_path=tmp_path / "currens.db")


def test_collector_stores_a_good_ecb_answer(monkeypatch, collector_db) -> None:
    monkeypatch.setattr(
        core,
        "get_exchange_rates_from_european_central_bank",
        lambda *args, **kwargs: [{"date": "2025-04-16", "value": 1.25}],
    )

    core.store_european_central_bank_rates(1, 2, start_date="2025-04-16")

    _no_network(monkeypatch)
    assert get_rate(DAY, "EUR", "USD", db_path=collector_db) == Decimal("1.25")
