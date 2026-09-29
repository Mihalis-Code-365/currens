from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from currens.service import (
    configure_service_database,
    get_rate,
    has_rate,
    import_mihalis_rates,
)


def _create_mihalis_source_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE currencies (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE exchange_rates (
            source TEXT,
            exchange_rate_date TEXT NOT NULL,
            base_currency_id INTEGER NOT NULL,
            target_currency_id INTEGER NOT NULL,
            value REAL NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO currencies (id, name) VALUES (?, ?)",
        [(1, "EUR"), (2, "USD"), (3, "SEK")],
    )
    connection.executemany(
        """
        INSERT INTO exchange_rates (source, exchange_rate_date, base_currency_id, target_currency_id, value)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("fixture", "2025-06-02", 1, 3, 11.10),
            ("fixture", "2025-06-02", 1, 3, 11.10),
            ("fixture", "2025-06-02", 2, 1, 0.88),
        ],
    )
    connection.commit()
    connection.close()


def test_import_mihalis_rates_bootstraps_currens_db(tmp_path) -> None:
    source_db = tmp_path / "mihalis.db"
    currens_db = tmp_path / "currens.db"
    _create_mihalis_source_db(source_db)

    configure_service_database(currens_db)
    imported = import_mihalis_rates(source_db, currens_db_path=currens_db)

    assert imported == 3
    assert has_rate(date(2025, 6, 2), "EUR", "SEK", db_path=currens_db) is True
    assert get_rate(date(2025, 6, 2), "EUR", "SEK", db_path=currens_db) == Decimal("11.1")
    assert get_rate(date(2025, 6, 2), "SEK", "EUR", db_path=currens_db).quantize(
        Decimal("0.0000000001")
    ) == (Decimal("1") / Decimal("11.1")).quantize(Decimal("0.0000000001"))


def test_import_mihalis_rates_deduplicates_on_currens_primary_key(tmp_path) -> None:
    source_db = tmp_path / "mihalis.db"
    currens_db = tmp_path / "currens.db"
    _create_mihalis_source_db(source_db)

    configure_service_database(currens_db)
    import_mihalis_rates(source_db, currens_db_path=currens_db)

    connection = sqlite3.connect(currens_db)
    try:
        count = connection.execute("SELECT COUNT(*) FROM exchange_rates").fetchone()[0]
    finally:
        connection.close()

    assert count == 2


def _create_portfolio_v3_source_db(path: Path) -> None:
    """Mihalis' v3 `portfolio.db` layout: TEXT currency codes, and `rate_date`/`rate` where the
    legacy schema had `exchange_rate_date`/`value`. Mirrors that project's migrations/schema.sql.

    The TEXT-code support added in f379c68 had no fixture of its own, so nothing noticed that it
    still selected the legacy date and value column names and could not read a v3 database at all.
    """
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE currencies (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE exchange_rates (
            source TEXT NOT NULL,
            rate_date TEXT NOT NULL,
            base_currency TEXT NOT NULL,
            target_currency TEXT NOT NULL,
            rate REAL NOT NULL,
            PRIMARY KEY (rate_date, base_currency, target_currency)
        );
        """
    )
    connection.executemany(
        "INSERT INTO currencies (code, name) VALUES (?, ?)",
        [("EUR", "Euro"), ("USD", "United States Dollar"), ("SEK", "Swedish Krona")],
    )
    connection.executemany(
        """
        INSERT INTO exchange_rates (source, rate_date, base_currency, target_currency, rate)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("EuropeanCentralBank", "2025-06-02", "EUR", "SEK", 11.10),
            ("EuropeanCentralBank", "2025-06-02", "EUR", "USD", 1.14),
        ],
    )
    connection.commit()
    connection.close()


def test_import_mihalis_rates_reads_portfolio_v3_schema(tmp_path) -> None:
    source_db = tmp_path / "portfolio.db"
    currens_db = tmp_path / "currens.db"
    _create_portfolio_v3_source_db(source_db)

    configure_service_database(currens_db)
    imported = import_mihalis_rates(source_db, currens_db_path=currens_db)

    assert imported == 2
    assert get_rate(date(2025, 6, 2), "EUR", "SEK", db_path=currens_db) == Decimal("11.1")
    assert get_rate(date(2025, 6, 2), "EUR", "USD", db_path=currens_db) == Decimal("1.14")


def test_schema_migration_drops_only_wrong_riksbank_rows(tmp_path) -> None:
    currens_db = tmp_path / "currens.db"
    configure_service_database(currens_db)
    connection = sqlite3.connect(currens_db)
    try:
        # Simulate a pre-v1 cache.
        connection.execute("PRAGMA user_version = 0")
        connection.executemany(
            "INSERT INTO exchange_rates VALUES (?, ?, ?, ?, ?)",
            [
                # Inverted by the pre-v1 service fetch (issue #10).
                ("Riksbank", "2025-06-02", 2, 3, 0.1),
                ("Riksbank", "2025-06-03", 3, 2, 9.8),
                # Legacy collector: Riksbank series stored without SEK in the pair.
                ("riksbank", "2025-06-02", 1, 2, 9.7),
                # Correct rows, e.g. imported from Mihalis.
                ("Riksbank", "2025-06-04", 3, 2, 0.1),
                ("Riksbank", "2025-06-04", 1, 3, 11.0),
                ("EuropeanCentralBank", "2025-06-02", 1, 3, 11.1),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    configure_service_database(currens_db)

    connection = sqlite3.connect(currens_db)
    try:
        remaining = connection.execute(
            "SELECT exchange_rate_date, base_currency_id, target_currency_id FROM exchange_rates"
            " ORDER BY 1, 2, 3"
        ).fetchall()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    assert remaining == [("2025-06-02", 1, 3), ("2025-06-04", 1, 3), ("2025-06-04", 3, 2)]
    assert version == 1
    assert get_rate(date(2025, 6, 4), "USD", "SEK", db_path=currens_db) == Decimal("10")
