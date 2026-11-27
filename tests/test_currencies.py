"""#24: currencies beyond EUR/USD/SEK, routed to the ECB or the Riksbank, cross pairs refused."""

from __future__ import annotations

import io
import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from currens import (
    RateNotPublishedError,
    UnsupportedCurrencyError,
    ensure_rates,
    ensure_rates_for_dates,
    get_rate,
    get_rate_record,
    get_rates_for_period,
    has_rate,
)
from currens.apis import rate_sources
from currens.utils.currency import CURRENCIES, get_currency_iso_code_by_id

DAY = date(2025, 4, 16)


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _record_urls(monkeypatch, body: bytes) -> list[str]:
    urls: list[str] = []

    def fake_urlopen(url, timeout):
        urls.append(url)
        return _Response(body)

    monkeypatch.setattr(rate_sources, "urlopen", fake_urlopen)
    return urls


def _ecb_body(value: float) -> bytes:
    return (
        b'{"dataSets":[{"series":{"0:0:0:0:0":{"observations":{"0":[%s]}}}}],'
        b'"structure":{"dimensions":{"observation":[{"values":[{"id":"2025-04-16"}]}]}}}'
        % str(value).encode()
    )


def test_currency_table_keeps_legacy_ids_and_is_unique() -> None:
    assert CURRENCIES[:3] == ((1, "EUR", "Euro"), (2, "USD", "US dollar"), (3, "SEK", "Swedish krona"))
    ids = [row[0] for row in CURRENCIES]
    codes = [row[1] for row in CURRENCIES]
    assert ids == list(range(1, len(CURRENCIES) + 1))
    assert len(set(codes)) == len(codes)
    assert all(len(code) == 3 and code.isupper() for code in codes)


def test_foreign_to_sek_uses_the_riksbank_series_for_that_currency(monkeypatch, tmp_path) -> None:
    urls = _record_urls(monkeypatch, b'[{"date":"2025-04-16","value":12.9}]')

    rate = get_rate(DAY, "GBP", "SEK", db_path=tmp_path / "c.db")

    assert rate == Decimal("12.9")
    assert urls == ["https://api.riksbank.se/swea/v1/Observations/SEKGBPPMI/2025-04-16/2025-04-16"]


def test_sek_to_foreign_inverts_the_riksbank_quote(monkeypatch, tmp_path) -> None:
    urls = _record_urls(monkeypatch, b'[{"date":"2025-04-16","value":0.064}]')

    assert get_rate(DAY, "SEK", "JPY", db_path=tmp_path / "c.db") == Decimal("1") / Decimal("0.064")
    assert urls == ["https://api.riksbank.se/swea/v1/Observations/SEKJPYPMI/2025-04-16/2025-04-16"]


def test_eur_to_foreign_uses_the_ecb_series_directly(monkeypatch, tmp_path) -> None:
    urls = _record_urls(monkeypatch, _ecb_body(1.5937))

    assert get_rate(DAY, "EUR", "AUD", db_path=tmp_path / "c.db") == Decimal("1.5937")
    assert "/EXR/D.AUD.EUR.SP00.A?" in urls[0]


def test_foreign_to_eur_uses_the_ecb_series_inverted(monkeypatch, tmp_path) -> None:
    urls = _record_urls(monkeypatch, _ecb_body(11.5))

    rate = get_rate(DAY, "NOK", "EUR", db_path=tmp_path / "c.db")

    # The cache stores a REAL, so the reciprocal comes back at float precision.
    assert float(rate) == pytest.approx(1 / 11.5, rel=1e-15)
    assert "/EXR/D.NOK.EUR.SP00.A?" in urls[0]


def test_pair_without_eur_or_sek_is_refused_without_a_fetch(monkeypatch, tmp_path) -> None:
    urls = _record_urls(monkeypatch, b"[]")

    with pytest.raises(UnsupportedCurrencyError, match="GBP->NOK") as excinfo:
        get_rate(DAY, "GBP", "NOK", db_path=tmp_path / "c.db")

    # A caller walking back day by day must stop, not treat this as "no rate on this date".
    assert not isinstance(excinfo.value, RateNotPublishedError)
    assert isinstance(excinfo.value, ValueError)
    assert urls == []


@pytest.mark.parametrize(
    "call",
    [
        lambda db: ensure_rates([("GBP", "NOK")], DAY, DAY, db_path=db),
        lambda db: ensure_rates_for_dates([("GBP", "NOK")], [DAY], db_path=db),
        lambda db: get_rates_for_period("GBP", "NOK", DAY, DAY, db_path=db),
    ],
    ids=["ensure_rates", "ensure_rates_for_dates", "get_rates_for_period"],
)
def test_every_fetching_entry_point_refuses_a_cross_pair(monkeypatch, tmp_path, call) -> None:
    urls = _record_urls(monkeypatch, b"[]")

    with pytest.raises(UnsupportedCurrencyError):
        call(tmp_path / "c.db")
    assert urls == []


def test_unknown_currency_code_is_unsupported(monkeypatch, tmp_path) -> None:
    urls = _record_urls(monkeypatch, b"[]")

    with pytest.raises(UnsupportedCurrencyError, match="XYZ"):
        get_rate(DAY, "XYZ", "SEK", db_path=tmp_path / "c.db")
    assert urls == []


def test_unknown_currency_id_is_unsupported() -> None:
    with pytest.raises(UnsupportedCurrencyError, match="99"):
        get_currency_iso_code_by_id(99)


def test_existing_cache_gains_the_new_currency_rows(tmp_path) -> None:
    db = tmp_path / "c.db"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE currencies (id INTEGER NOT NULL PRIMARY KEY, name TEXT NOT NULL,
                                 iso_code TEXT NOT NULL UNIQUE);
        INSERT INTO currencies VALUES (1, 'EUR', 'EUR'), (2, 'USD', 'USD'), (3, 'SEK', 'SEK');
        """
    )
    connection.commit()
    connection.close()

    get_rate(DAY, "SEK", "SEK", db_path=db)

    with sqlite3.connect(db) as connection:
        rows = connection.execute("SELECT id, iso_code FROM currencies ORDER BY id").fetchall()
    assert rows == [(currency_id, code) for currency_id, code, _ in CURRENCIES]


SATURDAY = date(2025, 4, 19)
SUNDAY = date(2025, 4, 20)


@pytest.mark.parametrize(
    "call",
    [
        lambda db: ensure_rates([("GBP", "NOK")], SATURDAY, SUNDAY, db_path=db),
        lambda db: get_rates_for_period("GBP", "NOK", SATURDAY, SUNDAY, db_path=db),
    ],
    ids=["ensure_rates", "get_rates_for_period"],
)
def test_cross_pair_is_refused_on_a_range_without_business_dates(monkeypatch, tmp_path, call) -> None:
    # #31: no business dates meant the coverage check passed before routing was checked.
    urls = _record_urls(monkeypatch, b"[]")

    with pytest.raises(UnsupportedCurrencyError, match="GBP->NOK"):
        call(tmp_path / "c.db")
    assert urls == []


def _cache_with_cross_pair_row(db) -> None:
    get_rate(DAY, "SEK", "SEK", db_path=db)  # creates the schema
    with sqlite3.connect(db) as connection:
        gbp, nok = (
            connection.execute("SELECT id FROM currencies WHERE iso_code = ?", (code,)).fetchone()[0]
            for code in ("GBP", "NOK")
        )
        # Both directions, so neither a direct nor an inverted lookup can serve the pair.
        connection.executemany(
            "INSERT INTO exchange_rates (source, exchange_rate_date, base_currency_id,"
            " target_currency_id, value) VALUES (?, ?, ?, ?, ?)",
            [("fixture", DAY.isoformat(), gbp, nok, 13.5), ("fixture", DAY.isoformat(), nok, gbp, 0.074)],
        )


@pytest.mark.parametrize(
    "call",
    [
        lambda db: get_rate(DAY, "GBP", "NOK", db_path=db),
        lambda db: get_rate_record(DAY, "GBP", "NOK", db_path=db),
        lambda db: ensure_rates([("GBP", "NOK")], DAY, DAY, db_path=db),
        lambda db: ensure_rates_for_dates([("GBP", "NOK")], [DAY], db_path=db),
        lambda db: get_rates_for_period("GBP", "NOK", DAY, DAY, db_path=db),
    ],
    ids=["get_rate", "get_rate_record", "ensure_rates", "ensure_rates_for_dates", "get_rates_for_period"],
)
def test_cached_cross_pair_row_is_not_served(monkeypatch, tmp_path, call) -> None:
    # #31: a GBP->NOK row in the cache (e.g. written by hand or an older currens) bypassed the refusal.
    urls = _record_urls(monkeypatch, b"[]")
    db = tmp_path / "c.db"
    _cache_with_cross_pair_row(db)

    with pytest.raises(UnsupportedCurrencyError, match="GBP->NOK"):
        call(db)
    assert urls == []


def test_has_rate_stays_a_boolean_for_a_cross_pair(tmp_path) -> None:
    db = tmp_path / "c.db"
    _cache_with_cross_pair_row(db)

    assert has_rate(DAY, "GBP", "NOK", db_path=db) is False


def test_has_rate_is_false_for_the_cross_pair_in_either_direction(tmp_path) -> None:
    db = tmp_path / "c.db"
    _cache_with_cross_pair_row(db)

    assert has_rate(DAY, "NOK", "GBP", db_path=db) is False


def test_identity_pair_outside_eur_and_sek_is_still_one(monkeypatch, tmp_path) -> None:
    urls = _record_urls(monkeypatch, b"[]")
    db = tmp_path / "c.db"

    assert get_rate(DAY, "GBP", "GBP", db_path=db) == Decimal("1")
    assert get_rate_record(DAY, "GBP", "GBP", db_path=db).source == "identity"
    assert has_rate(DAY, "GBP", "GBP", db_path=db) is True
    ensure_rates([("GBP", "GBP")], DAY, DAY, db_path=db)
    assert urls == []


def test_unknown_code_in_a_cross_pair_reports_the_unknown_code(tmp_path) -> None:
    with pytest.raises(UnsupportedCurrencyError, match="Unsupported currency 'XYZ'"):
        get_rate(DAY, "XYZ", "NOK", db_path=tmp_path / "c.db")
