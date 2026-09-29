from __future__ import annotations

import builtins
from datetime import date
from decimal import Decimal

from currens.collector import core
from currens.db import session as db_session
from currens.db.models import Base
from currens.service import get_rate, has_rate


def test_store_riksbank_rates_stores_foreign_to_sek(monkeypatch, tmp_path) -> None:
    currens_db = tmp_path / "currens.db"
    db_session.configure_database(db_path=currens_db)
    monkeypatch.setattr(builtins, "_", lambda message: message, raising=False)
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
