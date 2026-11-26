"""#36: `store_rates` loads caller-supplied rates; `using_service_database` points currens at a cache."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import currens
from currens import (
    UnsupportedCurrencyError,
    get_rate,
    get_rate_record,
    store_rates,
    using_service_database,
)
from currens import service
from currens.apis import rate_sources

DAY = date(2024, 1, 2)


@pytest.fixture(autouse=True)
def _restore_path_and_refuse_network(monkeypatch):
    """Every test here works offline, and none leaves currens pointing at its scratch file."""
    monkeypatch.setattr(service, "_CURRENT_DB_PATH", service._CURRENT_DB_PATH)

    def refuse(*args, **kwargs):
        raise AssertionError("no network in these tests")

    monkeypatch.setattr(rate_sources, "urlopen", refuse)


def _rows(db: Path) -> int:
    with sqlite3.connect(db) as connection:
        return connection.execute("SELECT COUNT(*) FROM exchange_rates").fetchone()[0]


# --- store_rates ------------------------------------------------------------------------------


def test_stored_rate_is_served_without_a_fetch(tmp_path) -> None:
    db = tmp_path / "c.db"
    store_rates(
        [(DAY, "usd", "SEK", Decimal("10.5"))], source="fixture", origin="fixture", currens_db_path=db
    )

    assert get_rate(DAY, "USD", "SEK", db_path=db) == Decimal("10.5")
    record = get_rate_record(DAY, "USD", "SEK", db_path=db)
    assert record.origin == "fixture"
    assert record.series_id is None
    assert record.fetched_at is None
    assert record.inverted is None


def test_returns_the_number_of_rows(tmp_path) -> None:
    rows = [
        (DAY, "USD", "SEK", Decimal("10.5")),
        (date(2024, 1, 3), "USD", "SEK", Decimal("10.6")),
        (DAY, "EUR", "USD", Decimal("1.1")),
    ]
    assert store_rates(rows, source="fixture", origin="o", currens_db_path=tmp_path / "c.db") == 3


@pytest.mark.parametrize(
    ("bad", "error"),
    [
        pytest.param(("XXX", "SEK", Decimal("1")), UnsupportedCurrencyError, id="unknown-code"),
        pytest.param(("USD", "NOK", Decimal("1")), UnsupportedCurrencyError, id="no-eur-or-sek"),
        pytest.param(("SEK", "SEK", Decimal("1")), ValueError, id="same-currency"),
        pytest.param(("USD", "SEK", Decimal(0)), ValueError, id="zero"),
        pytest.param(("USD", "SEK", Decimal("NaN")), ValueError, id="nan"),
        pytest.param(("USD", "SEK", Decimal("Infinity")), ValueError, id="infinity"),
    ],
)
def test_one_bad_row_writes_nothing(tmp_path, bad, error) -> None:
    db = tmp_path / "c.db"
    base, target, rate = bad
    rows = [
        (DAY, "USD", "SEK", Decimal("10.5")),
        (date(2024, 1, 3), base, target, rate),
        (date(2024, 1, 4), "USD", "SEK", Decimal("10.7")),
    ]
    with pytest.raises(error, match="2024-01-03"):
        store_rates(rows, source="fixture", origin="o", currens_db_path=db)
    assert _rows(db) == 0


def test_the_error_names_the_pair(tmp_path) -> None:
    with pytest.raises(UnsupportedCurrencyError, match=r"2024-01-02 USD->NOK"):
        store_rates(
            [(DAY, "usd", "nok", Decimal("1"))],
            source="fixture",
            origin="o",
            currens_db_path=tmp_path / "c.db",
        )


@pytest.mark.parametrize(
    "when",
    [pytest.param(datetime(2024, 1, 2), id="datetime"), pytest.param("2024-01-02", id="string")],
)
def test_a_date_that_is_not_a_plain_date_is_refused(tmp_path, when) -> None:
    db = tmp_path / "c.db"
    with pytest.raises(TypeError):
        store_rates([(when, "USD", "SEK", Decimal("10.5"))], source="f", origin="o", currens_db_path=db)
    assert _rows(db) == 0


@pytest.mark.parametrize(
    ("source", "origin", "named"),
    [pytest.param(" ", "o", "source", id="source"), pytest.param("f", "", "origin", id="origin")],
)
def test_empty_source_or_origin_is_refused(tmp_path, source, origin, named) -> None:
    with pytest.raises(ValueError, match=named):
        store_rates(
            [(DAY, "USD", "SEK", Decimal("10.5"))],
            source=source,
            origin=origin,
            currens_db_path=tmp_path / "c.db",
        )


def test_existing_row_from_another_source_still_wins(tmp_path) -> None:
    db = tmp_path / "c.db"
    store_rates([(DAY, "EUR", "USD", Decimal("1.1"))], source="ECB", origin="provider", currens_db_path=db)
    store_rates([(DAY, "EUR", "USD", Decimal("2.0"))], source="fixture", origin="f", currens_db_path=db)

    assert get_rate(DAY, "EUR", "USD", db_path=db) == Decimal("1.1")


def test_same_source_replaces(tmp_path) -> None:
    db = tmp_path / "c.db"
    store_rates([(DAY, "EUR", "USD", Decimal("1.1"))], source="fixture", origin="f", currens_db_path=db)
    store_rates([(DAY, "EUR", "USD", Decimal("1.2"))], source="fixture", origin="g", currens_db_path=db)

    assert get_rate(DAY, "EUR", "USD", db_path=db) == Decimal("1.2")
    assert get_rate_record(DAY, "EUR", "USD", db_path=db).origin == "g"
    assert _rows(db) == 1


def test_replacing_a_row_keeps_its_place(tmp_path) -> None:
    db = tmp_path / "c.db"
    store_rates([(DAY, "EUR", "USD", Decimal("1.1"))], source="A", origin="o", currens_db_path=db)
    store_rates([(DAY, "EUR", "USD", Decimal("1.2"))], source="B", origin="o", currens_db_path=db)
    store_rates([(DAY, "EUR", "USD", Decimal("1.3"))], source="A", origin="o", currens_db_path=db)

    assert get_rate(DAY, "EUR", "USD", db_path=db) == Decimal("1.3")


def test_replacing_clears_a_fetched_rows_provenance(tmp_path) -> None:
    db = tmp_path / "c.db"
    store_rates([(DAY, "EUR", "USD", Decimal("1.1"))], source="ECB", origin="o", currens_db_path=db)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE exchange_rates SET series_id = 'EXR', fetched_at = '2024-01-02T16:00:00+00:00',"
            " inverted = 0"
        )
    store_rates([(DAY, "EUR", "USD", Decimal("1.2"))], source="ECB", origin="f", currens_db_path=db)

    record = get_rate_record(DAY, "EUR", "USD", db_path=db)
    assert (record.series_id, record.fetched_at, record.inverted) == (None, None, None)


# --- using_service_database -------------------------------------------------------------------


def test_using_service_database_points_and_restores(tmp_path) -> None:
    before = service._CURRENT_DB_PATH
    db = tmp_path / "c.db"
    with using_service_database(db) as resolved:
        assert resolved == db.resolve()
        store_rates([(DAY, "USD", "SEK", Decimal("10.5"))], source="f", origin="o")
    assert _rows(db) == 1
    assert service._CURRENT_DB_PATH == before


def test_restores_after_an_exception(tmp_path) -> None:
    before = service._CURRENT_DB_PATH
    with pytest.raises(RuntimeError), using_service_database(tmp_path / "c.db"):
        raise RuntimeError("boom")
    assert service._CURRENT_DB_PATH == before


def test_nested_blocks_restore_in_order(tmp_path) -> None:
    before = service._CURRENT_DB_PATH
    outer, inner = tmp_path / "a.db", tmp_path / "b.db"
    with using_service_database(outer):
        with using_service_database(inner):
            assert service._CURRENT_DB_PATH == inner.resolve()
        assert service._CURRENT_DB_PATH == outer.resolve()
    assert service._CURRENT_DB_PATH == before


def test_exit_does_not_create_the_previous_file(tmp_path, monkeypatch) -> None:
    never = tmp_path / "never" / "c.db"
    monkeypatch.setattr(service, "_CURRENT_DB_PATH", never)
    with using_service_database(tmp_path / "other.db"):
        pass
    assert service._CURRENT_DB_PATH == never
    assert not never.parent.exists()


def test_both_are_exported() -> None:
    assert "store_rates" in currens.__all__
    assert "using_service_database" in currens.__all__


# --- the reverse direction, other inputs, an old cache ------------------------------------------


@pytest.mark.parametrize("source", ["ECB", "fixture"])
def test_a_row_whose_reverse_is_cached_is_refused(tmp_path, source) -> None:
    # A lookup serves USD->EUR from an EUR->USD row when it has none of its own, so a stored
    # USD->EUR row would be served instead of 1/1.1.
    db = tmp_path / "c.db"
    store_rates([(DAY, "EUR", "USD", Decimal("1.1"))], source="ECB", origin="p", currens_db_path=db)
    with pytest.raises(ValueError, match=r"2024-01-02 USD->EUR: the cache already holds the reverse"):
        store_rates([(DAY, "USD", "EUR", Decimal("0.5"))], source=source, origin="f", currens_db_path=db)

    assert get_rate(DAY, "USD", "EUR", db_path=db) == Decimal("1") / Decimal("1.1")
    assert _rows(db) == 1


def test_a_batch_holding_both_directions_is_refused(tmp_path) -> None:
    db = tmp_path / "c.db"
    rows = [(DAY, "EUR", "USD", Decimal("1.1")), (DAY, "USD", "EUR", Decimal("0.5"))]
    with pytest.raises(ValueError, match="the batch also holds the reverse"):
        store_rates(rows, source="f", origin="o", currens_db_path=db)
    assert _rows(db) == 0


def test_the_reverse_on_another_date_is_no_obstacle(tmp_path) -> None:
    db = tmp_path / "c.db"
    store_rates([(DAY, "EUR", "USD", Decimal("1.1"))], source="ECB", origin="p", currens_db_path=db)
    store_rates(
        [(date(2024, 1, 3), "USD", "EUR", Decimal("0.9"))], source="f", origin="o", currens_db_path=db
    )
    assert get_rate(date(2024, 1, 3), "USD", "EUR", db_path=db) == Decimal("0.9")


@pytest.mark.parametrize(
    ("rows", "source", "origin"),
    [
        pytest.param([(DAY, "USD", "SEK", Decimal("1"))], None, "o", id="source-none"),
        pytest.param([(DAY, "USD", "SEK", Decimal("1"))], "f", 3, id="origin-int"),
        pytest.param([(DAY, None, "SEK", Decimal("1"))], "f", "o", id="code-none"),
        pytest.param([(DAY, "USD", "SEK")], "f", "o", id="three-tuple"),
        pytest.param([(DAY, "USD", "SEK", 10.5)], "f", "o", id="float-rate"),
    ],
)
def test_inputs_of_the_wrong_type_are_a_type_error(tmp_path, rows, source, origin) -> None:
    db = tmp_path / "c.db"
    with pytest.raises(TypeError):
        store_rates(rows, source=source, origin=origin, currens_db_path=db)
    assert _rows(db) == 0


def test_an_old_cache_without_provenance_columns_is_upserted_in_place(tmp_path) -> None:
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE currencies (id INTEGER NOT NULL PRIMARY KEY, name TEXT NOT NULL,
                                     iso_code TEXT NOT NULL UNIQUE);
            CREATE TABLE exchange_rates (
                source TEXT NOT NULL, exchange_rate_date TEXT NOT NULL,
                base_currency_id INTEGER NOT NULL, target_currency_id INTEGER NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (source, exchange_rate_date, base_currency_id, target_currency_id));
            INSERT INTO exchange_rates VALUES ('A', '2024-01-02', 1, 2, 1.1);
            INSERT INTO exchange_rates VALUES ('B', '2024-01-02', 1, 2, 1.2);
            PRAGMA user_version = 1;
            """
        )
    store_rates([(DAY, "EUR", "USD", Decimal("1.3"))], source="A", origin="o", currens_db_path=db)

    assert get_rate(DAY, "EUR", "USD", db_path=db) == Decimal("1.3")
    assert get_rate_record(DAY, "EUR", "USD", db_path=db).origin == "o"
    assert _rows(db) == 2
