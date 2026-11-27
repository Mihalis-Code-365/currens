from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

from currens.service import (
    configure_service_database,
    get_rate,
)


def test_schema_migration_drops_only_wrong_riksbank_rows(tmp_path) -> None:
    currens_db = tmp_path / "currens.db"
    configure_service_database(currens_db)
    connection = sqlite3.connect(currens_db)
    try:
        # Simulate a pre-v1 cache.
        connection.execute("PRAGMA user_version = 0")
        connection.executemany(
            "INSERT INTO exchange_rates (source, exchange_rate_date, base_currency_id,"
            " target_currency_id, value) VALUES (?, ?, ?, ?, ?)",
            [
                # Inverted by the pre-v1 service fetch (issue #10).
                ("Riksbank", "2025-06-02", 2, 3, 0.1),
                ("Riksbank", "2025-06-03", 3, 2, 9.8),
                # Legacy collector: Riksbank series stored without SEK in the pair.
                ("riksbank", "2025-06-02", 1, 2, 9.7),
                # Correct rows, e.g. loaded with `store_rates`.
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
    assert version == 2
    assert get_rate(date(2025, 6, 4), "USD", "SEK", db_path=currens_db) == Decimal("10")


def test_exchange_rate_record_keeps_old_field_names_as_aliases() -> None:
    from currens.service import ExchangeRateRecord

    record = ExchangeRateRecord(
        exchange_rate_date=date(2025, 4, 14),
        rate=Decimal("1.1377"),
        source="EuropeanCentralBank",
        base_currency_id=1,
        rate_currency_id=2,
    )

    assert record.value == record.rate == Decimal("1.1377")
    assert record.target_currency_id == record.rate_currency_id == 2
