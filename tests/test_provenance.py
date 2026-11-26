"""#23: each cached rate records its provider series, fetch time, direction and origin."""

from __future__ import annotations

import io
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from currens import get_rate, get_rate_record, get_rates_for_period, import_mihalis_rates
from currens.apis import rate_sources

DAY = date(2025, 4, 16)


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _answer(monkeypatch, body: bytes) -> None:
    monkeypatch.setattr(rate_sources, "urlopen", lambda url, timeout: _Response(body))


def _ecb_body(value: float) -> bytes:
    return (
        b'{"dataSets":[{"series":{"0:0:0:0:0":{"observations":{"0":[%s]}}}}],'
        b'"structure":{"dimensions":{"observation":[{"values":[{"id":"2025-04-16"}]}]}}}'
        % str(value).encode()
    )


def test_riksbank_fetch_records_series_time_and_direction(monkeypatch, tmp_path) -> None:
    _answer(monkeypatch, b'[{"date":"2025-04-16","value":10.0}]')
    before = datetime.now(UTC)

    record = get_rate_record(DAY, "SEK", "USD", db_path=tmp_path / "c.db")

    assert record.rate == Decimal("0.1")
    assert record.series_id == "SEKUSDPMI"
    assert record.inverted is True  # SEK->USD is 1 / (SEK per USD)
    assert record.origin == "provider"
    assert record.source == "Riksbank"
    assert record.fetched_at is not None
    assert record.fetched_at.tzinfo is not None
    assert before <= record.fetched_at <= datetime.now(UTC)


def test_ecb_fetch_records_the_series_actually_requested(monkeypatch, tmp_path) -> None:
    _answer(monkeypatch, _ecb_body(11.0))

    record = get_rate_record(DAY, "SEK", "EUR", db_path=tmp_path / "c.db")

    assert record.series_id == "EXR/D.SEK.EUR.SP00.A"
    assert record.inverted is True


def test_reverse_lookup_flips_inverted_and_keeps_the_rest(monkeypatch, tmp_path) -> None:
    db = tmp_path / "c.db"
    _answer(monkeypatch, _ecb_body(11.0))
    stored = get_rate_record(DAY, "EUR", "SEK", db_path=db)

    served = get_rate_record(DAY, "SEK", "EUR", db_path=db)  # from the cached EUR->SEK row

    assert stored.inverted is False
    assert served.inverted is True
    assert (served.base_currency_id, served.rate_currency_id) == (3, 1)
    assert served.rate == get_rate(DAY, "SEK", "EUR", db_path=db)
    assert (served.series_id, served.fetched_at) == (stored.series_id, stored.fetched_at)


def test_get_rates_for_period_carries_provenance(monkeypatch, tmp_path) -> None:
    _answer(monkeypatch, _ecb_body(11.0))

    (record,) = get_rates_for_period("EUR", "SEK", DAY, DAY, db_path=tmp_path / "c.db")

    assert (record.series_id, record.inverted, record.origin) == ("EXR/D.SEK.EUR.SP00.A", False, "provider")


def test_imported_rows_name_the_file_and_leave_the_rest_unknown(tmp_path) -> None:
    source = tmp_path / "portfolio.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE exchange_rates (source TEXT, rate_date TEXT,"
            " base_currency TEXT, target_currency TEXT, rate REAL)"
        )
        connection.execute("INSERT INTO exchange_rates VALUES ('Riksbank', '2025-04-16', 'USD', 'SEK', 10.0)")
    db = tmp_path / "c.db"
    import_mihalis_rates(source, currens_db_path=db)

    record = get_rate_record(DAY, "USD", "SEK", db_path=db)

    assert record.origin == f"import_mihalis_rates:{source.resolve()}"
    assert (record.series_id, record.fetched_at, record.inverted) == (None, None, None)


def test_v1_cache_is_migrated_and_old_rows_keep_nulls(tmp_path) -> None:
    db = tmp_path / "c.db"
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE currencies (id INTEGER NOT NULL PRIMARY KEY, name TEXT NOT NULL,
                                     iso_code TEXT NOT NULL UNIQUE);
            CREATE TABLE exchange_rates (source TEXT NOT NULL, exchange_rate_date TEXT NOT NULL,
                base_currency_id INTEGER NOT NULL, target_currency_id INTEGER NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (source, exchange_rate_date, base_currency_id, target_currency_id));
            INSERT INTO exchange_rates VALUES ('EuropeanCentralBank', '2025-04-16', 1, 3, 11.0);
            PRAGMA user_version = 1;
            """
        )

    record = get_rate_record(DAY, "EUR", "SEK", db_path=db)

    assert record.rate == Decimal("11.0")
    assert (record.series_id, record.fetched_at, record.inverted, record.origin) == (None,) * 4
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_identity_record(tmp_path) -> None:
    record = get_rate_record(DAY, "SEK", "SEK", db_path=tmp_path / "c.db")

    assert (record.rate, record.source, record.series_id) == (Decimal("1"), "identity", None)


@pytest.mark.parametrize("pair", [("USD", "SEK"), ("SEK", "USD")])
def test_get_rate_is_unchanged_by_provenance(monkeypatch, tmp_path, pair) -> None:
    _answer(monkeypatch, b'[{"date":"2025-04-16","value":10.0}]')
    db = tmp_path / "c.db"

    assert get_rate(DAY, *pair, db_path=db) == get_rate_record(DAY, *pair, db_path=db).rate
