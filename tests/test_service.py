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
