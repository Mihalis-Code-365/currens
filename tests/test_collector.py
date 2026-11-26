from __future__ import annotations

from datetime import date
from decimal import Decimal

from currens.collector import core
from currens.db import session as db_session
from currens.db.models import Base
from currens.service import get_rate, get_rate_record, has_rate


def test_store_riksbank_rates_stores_foreign_to_sek(monkeypatch, tmp_path) -> None:
    currens_db = tmp_path / "currens.db"
    db_session.configure_database(db_path=currens_db)
    try:
        Base.metadata.create_all(bind=db_session.engine)
        # Riksbank SEKUSDPMI quotes SEK per 1 USD.
        monkeypatch.setattr(
            core,
            "get_exchange_rates_from_riksbank",
            lambda currency_id, start_date, end_date=None: [{"date": "2025-04-16", "value": 10.0}],
        )

        core.store_riksbank_rates(currency_id=2, start_date="2025-04-16")
    finally:
        db_session.configure_database()

    assert get_rate(date(2025, 4, 16), "USD", "SEK", db_path=currens_db) == Decimal("10")
    assert has_rate(date(2025, 4, 16), "EUR", "USD", db_path=currens_db) is False


def test_store_european_central_bank_rates_stores_eur_to_target(monkeypatch, tmp_path, capsys) -> None:
    currens_db = tmp_path / "currens.db"
    db_session.configure_database(db_path=currens_db)
    try:
        Base.metadata.create_all(bind=db_session.engine)
        # EXR/D.USD.EUR.SP00.A quotes USD per 1 EUR.
        monkeypatch.setattr(
            core,
            "get_exchange_rates_from_european_central_bank",
            lambda base_currency_id, rate_currency_id, start_date, end_date=None: [
                {"date": "2025-04-14", "value": 1.1377}
            ],
        )

        core.store_european_central_bank_rates(
            base_currency_id=1, rate_currency_id=2, start_date="2025-04-14", end_date="2025-04-14"
        )
    finally:
        db_session.configure_database()

    assert get_rate(date(2025, 4, 14), "EUR", "USD", db_path=currens_db) == Decimal("1.1377")
    record = get_rate_record(date(2025, 4, 14), "EUR", "USD", db_path=currens_db)
    assert (record.series_id, record.inverted, record.origin) == ("EXR/D.USD.EUR.SP00.A", False, "provider")
    output = capsys.readouterr().out
    assert "base currency EUR and rate currency USD from 2025-04-14 to 2025-04-14" in output
    assert "{" not in output


def test_store_european_central_bank_rates_without_end_date_says_today(monkeypatch, tmp_path, capsys) -> None:
    currens_db = tmp_path / "currens.db"
    db_session.configure_database(db_path=currens_db)
    try:
        Base.metadata.create_all(bind=db_session.engine)
        monkeypatch.setattr(
            core,
            "get_exchange_rates_from_european_central_bank",
            lambda base_currency_id, rate_currency_id, start_date, end_date=None: [],
        )

        core.store_european_central_bank_rates(base_currency_id=1, rate_currency_id=2, start_date="2025-04-14")
    finally:
        db_session.configure_database()

    assert "from 2025-04-14 to today..." in capsys.readouterr().out


def test_init_db_adds_missing_currencies_to_a_seeded_database(monkeypatch, tmp_path) -> None:
    from currens.db.models import Currency
    from currens.utils.currency import CURRENCIES

    db_session.configure_database(db_path=tmp_path / "collector.db")
    try:
        Base.metadata.create_all(bind=db_session.engine)
        with db_session.get_db_session() as session:
            session.add_all(
                [
                    Currency(id=1, name="Euro", iso_code="EUR"),
                    Currency(id=2, name="US Dollar", iso_code="USD"),
                    Currency(id=3, name="Swedish Krona", iso_code="SEK"),
                ]
            )
            session.commit()

        core.init_db()

        with db_session.get_db_session() as session:
            rows = session.query(Currency.id, Currency.iso_code).order_by(Currency.id).all()
    finally:
        db_session.configure_database()

    assert [tuple(row) for row in rows] == [(currency_id, code) for currency_id, code, _ in CURRENCIES]


def test_collector_writes_to_a_cache_made_before_provenance(monkeypatch, tmp_path) -> None:
    import sqlite3

    currens_db = tmp_path / "old.db"
    with sqlite3.connect(currens_db) as connection:
        connection.executescript(
            """
            CREATE TABLE currencies (id INTEGER NOT NULL PRIMARY KEY, name TEXT NOT NULL,
                                     iso_code TEXT NOT NULL UNIQUE);
            CREATE TABLE exchange_rates (source TEXT NOT NULL, exchange_rate_date TEXT NOT NULL,
                base_currency_id INTEGER NOT NULL, target_currency_id INTEGER NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (source, exchange_rate_date, base_currency_id, target_currency_id));
            PRAGMA user_version = 1;
            """
        )
    db_session.configure_database(db_path=currens_db)
    monkeypatch.setattr(
        core,
        "get_exchange_rates_from_riksbank",
        lambda currency_id, start_date, end_date=None: [{"date": "2025-04-16", "value": 10.0}],
    )
    try:
        core.store_riksbank_rates(currency_id=2, start_date="2025-04-16")
    finally:
        db_session.configure_database()

    record = get_rate_record(date(2025, 4, 16), "USD", "SEK", db_path=currens_db)
    assert (record.rate, record.series_id) == (Decimal("10"), "SEKUSDPMI")
