from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import math
import sqlite3
from typing import Iterable

from currens.apis.rate_sources import (
    get_exchange_rates_from_european_central_bank,
    get_exchange_rates_from_riksbank,
)


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "db" / "exchange_rates.db"
DEFAULT_CURRENCIES = (
    (1, "EUR", "EUR"),
    (2, "USD", "USD"),
    (3, "SEK", "SEK"),
)
EUR_CURRENCY_ID = 1
SEK_CURRENCY_ID = 3

_CURRENT_DB_PATH = DEFAULT_DB_PATH


@dataclass(frozen=True, slots=True)
class ExchangeRateRecord:
    exchange_rate_date: date
    value: Decimal
    source: str
    base_currency_id: int
    target_currency_id: int


def configure_service_database(db_path: str | Path | None = None) -> None:
    global _CURRENT_DB_PATH
    if db_path is not None:
        _CURRENT_DB_PATH = Path(db_path).expanduser().resolve()
    _CURRENT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_schema(_CURRENT_DB_PATH)


def import_mihalis_rates(
    source_db_path: str | Path,
    *,
    currens_db_path: str | Path | None = None,
) -> int:
    configure_service_database(currens_db_path)
    imported_count = 0
    with sqlite3.connect(Path(source_db_path).expanduser().resolve()) as source_connection:
        source_connection.row_factory = sqlite3.Row
        col_names = {
            row[0]
            for row in source_connection.execute(
                "SELECT name FROM pragma_table_info('exchange_rates')"
            ).fetchall()
        }
        use_text_codes = "base_currency" in col_names
        if use_text_codes:
            rows = source_connection.execute(
                """
                SELECT source, exchange_rate_date, base_currency, target_currency, value
                FROM exchange_rates
                ORDER BY exchange_rate_date ASC
                """
            ).fetchall()
        else:
            rows = source_connection.execute(
                """
                SELECT source, exchange_rate_date, base_currency_id, target_currency_id, value
                FROM exchange_rates
                ORDER BY exchange_rate_date ASC
                """
            ).fetchall()

    with _connect(_CURRENT_DB_PATH) as connection:
        for row in rows:
            if use_text_codes:
                base_id = _currency_id_from_code(str(row["base_currency"]))
                target_id = _currency_id_from_code(str(row["target_currency"]))
            else:
                base_id = int(row["base_currency_id"])
                target_id = int(row["target_currency_id"])
            connection.execute(
                """
                INSERT OR REPLACE INTO exchange_rates (
                    source,
                    exchange_rate_date,
                    base_currency_id,
                    target_currency_id,
                    value
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(row["source"] or "legacy"),
                    _coerce_date(row["exchange_rate_date"]).isoformat(),
                    base_id,
                    target_id,
                    float(row["value"]),
                ),
            )
            imported_count += 1
        connection.commit()
    return imported_count


def ensure_rates(
    pairs: Iterable[tuple[str, str]],
    start_date: date,
    end_date: date,
    *,
    db_path: str | Path | None = None,
) -> None:
    configure_service_database(db_path)
    for base_currency, target_currency in pairs:
        base_currency_id = _currency_id_from_code(base_currency)
        target_currency_id = _currency_id_from_code(target_currency)
        if base_currency_id == target_currency_id:
            continue
        if _has_complete_coverage(base_currency_id, target_currency_id, start_date, end_date):
            continue
        _fetch_and_store_pair(
            base_currency_id=base_currency_id,
            target_currency_id=target_currency_id,
            start_date=start_date,
            end_date=end_date,
            required_dates=None,
        )


def ensure_rates_for_dates(
    pairs: Iterable[tuple[str, str]],
    required_dates: Iterable[date],
    *,
    db_path: str | Path | None = None,
) -> None:
    configure_service_database(db_path)
    normalized_dates = sorted(set(required_dates))
    if not normalized_dates:
        return

    for base_currency, target_currency in pairs:
        base_currency_id = _currency_id_from_code(base_currency)
        target_currency_id = _currency_id_from_code(target_currency)
        if base_currency_id == target_currency_id:
            continue

        missing_dates = [
            current_date
            for current_date in normalized_dates
            if _lookup_rate(base_currency_id, target_currency_id, current_date) is None
        ]
        if not missing_dates:
            continue

        _fetch_and_store_pair(
            base_currency_id=base_currency_id,
            target_currency_id=target_currency_id,
            start_date=min(missing_dates),
            end_date=max(missing_dates),
            required_dates=missing_dates,
        )

        unresolved_dates = [
            current_date
            for current_date in missing_dates
            if _lookup_rate(base_currency_id, target_currency_id, current_date) is None
        ]
        if unresolved_dates:
            missing_labels = ", ".join(value.isoformat() for value in unresolved_dates[:5])
            raise ValueError(
                "Exchange rate provider did not return exact-date coverage for "
                f"{base_currency}->{target_currency}. Missing dates include: {missing_labels}"
            )


def has_rate(
    calculation_date: date,
    base_currency: str,
    target_currency: str,
    *,
    db_path: str | Path | None = None,
) -> bool:
    configure_service_database(db_path)
    base_currency_id = _currency_id_from_code(base_currency)
    target_currency_id = _currency_id_from_code(target_currency)
    if base_currency_id == target_currency_id:
        return True
    return _lookup_rate(base_currency_id, target_currency_id, calculation_date) is not None


def get_rate(
    calculation_date: date,
    base_currency: str,
    target_currency: str,
    *,
    db_path: str | Path | None = None,
) -> Decimal:
    configure_service_database(db_path)
    base_currency_id = _currency_id_from_code(base_currency)
    target_currency_id = _currency_id_from_code(target_currency)
    if base_currency_id == target_currency_id:
        return Decimal("1")

    rate = _lookup_rate(base_currency_id, target_currency_id, calculation_date)
    if rate is not None:
        return rate

    _fetch_and_store_pair(
        base_currency_id=base_currency_id,
        target_currency_id=target_currency_id,
        start_date=calculation_date,
        end_date=calculation_date,
        required_dates=[calculation_date],
    )
    rate = _lookup_rate(base_currency_id, target_currency_id, calculation_date)
    if rate is None:
        raise ValueError(
            f"Exchange rate not found for {base_currency} to {target_currency} on {calculation_date.isoformat()}"
        )
    return rate


def get_rates_for_period(
    base_currency: str,
    target_currency: str,
    start_date: date,
    end_date: date,
    *,
    db_path: str | Path | None = None,
) -> list[ExchangeRateRecord]:
    configure_service_database(db_path)
    ensure_rates([(base_currency, target_currency)], start_date, end_date, db_path=db_path)
    base_currency_id = _currency_id_from_code(base_currency)
    target_currency_id = _currency_id_from_code(target_currency)

    with _connect(_CURRENT_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT exchange_rate_date, value, source, base_currency_id, target_currency_id
            FROM exchange_rates
            WHERE base_currency_id = ?
              AND target_currency_id = ?
              AND exchange_rate_date BETWEEN ? AND ?
            ORDER BY exchange_rate_date ASC
            """,
            (
                base_currency_id,
                target_currency_id,
                start_date.isoformat(),
                end_date.isoformat(),
            ),
        ).fetchall()
    return [
        ExchangeRateRecord(
            exchange_rate_date=_coerce_date(row["exchange_rate_date"]),
            value=Decimal(str(row["value"])),
            source=str(row["source"]),
            base_currency_id=int(row["base_currency_id"]),
            target_currency_id=int(row["target_currency_id"]),
        )
        for row in rows
    ]


def _ensure_schema(db_path: Path) -> None:
    with _connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS currencies (
                id INTEGER NOT NULL PRIMARY KEY,
                name TEXT NOT NULL,
                iso_code TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS exchange_rates (
                source TEXT NOT NULL,
                exchange_rate_date TEXT NOT NULL,
                base_currency_id INTEGER NOT NULL,
                target_currency_id INTEGER NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (source, exchange_rate_date, base_currency_id, target_currency_id),
                FOREIGN KEY (base_currency_id) REFERENCES currencies (id),
                FOREIGN KEY (target_currency_id) REFERENCES currencies (id)
            );
            """
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO currencies (id, name, iso_code)
            VALUES (?, ?, ?)
            """,
            DEFAULT_CURRENCIES,
        )
        connection.commit()


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _currency_id_from_code(currency_code: str) -> int:
    normalized = currency_code.upper()
    for currency_id, _, iso_code in DEFAULT_CURRENCIES:
        if iso_code == normalized:
            return currency_id
    raise ValueError(f"Unsupported currency '{currency_code}'.")


def _lookup_rate(base_currency_id: int, target_currency_id: int, calculation_date: date) -> Decimal | None:
    with _connect(_CURRENT_DB_PATH) as connection:
        direct = connection.execute(
            """
            SELECT value
            FROM exchange_rates
            WHERE base_currency_id = ?
              AND target_currency_id = ?
              AND exchange_rate_date = ?
            LIMIT 1
            """,
            (base_currency_id, target_currency_id, calculation_date.isoformat()),
        ).fetchone()
        if direct is not None:
            return Decimal(str(direct["value"]))

        inverse = connection.execute(
            """
            SELECT value
            FROM exchange_rates
            WHERE base_currency_id = ?
              AND target_currency_id = ?
              AND exchange_rate_date = ?
            LIMIT 1
            """,
            (target_currency_id, base_currency_id, calculation_date.isoformat()),
        ).fetchone()
        if inverse is not None:
            return Decimal("1") / Decimal(str(inverse["value"]))
    return None


def _has_complete_coverage(
    base_currency_id: int,
    target_currency_id: int,
    start_date: date,
    end_date: date,
) -> bool:
    required_dates = _business_dates_between(start_date, end_date)
    if not required_dates:
        return True
    return all(
        _lookup_rate(base_currency_id, target_currency_id, current_date) is not None
        for current_date in required_dates
    )


def _business_dates_between(start_date: date, end_date: date) -> list[date]:
    current = start_date
    dates: list[date] = []
    while current <= end_date:
        if current.weekday() < 5:
            dates.append(current)
        current = date.fromordinal(current.toordinal() + 1)
    return dates


def _fetch_and_store_pair(
    *,
    base_currency_id: int,
    target_currency_id: int,
    start_date: date,
    end_date: date,
    required_dates: Iterable[date] | None,
) -> None:
    fetched_records = _fetch_pair_from_provider(
        base_currency_id=base_currency_id,
        target_currency_id=target_currency_id,
        start_date=start_date,
        end_date=end_date,
        required_dates=required_dates,
    )
    with _connect(_CURRENT_DB_PATH) as connection:
        connection.executemany(
            """
            INSERT OR REPLACE INTO exchange_rates (
                source,
                exchange_rate_date,
                base_currency_id,
                target_currency_id,
                value
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    record.source,
                    record.exchange_rate_date.isoformat(),
                    record.base_currency_id,
                    record.target_currency_id,
                    float(record.value),
                )
                for record in fetched_records
            ],
        )
        connection.commit()


def _fetch_pair_from_provider(
    *,
    base_currency_id: int,
    target_currency_id: int,
    start_date: date,
    end_date: date,
    required_dates: Iterable[date] | None,
) -> list[ExchangeRateRecord]:
    if EUR_CURRENCY_ID in (base_currency_id, target_currency_id):
        fetched = _fetch_from_ecb(
            base_currency_id=base_currency_id,
            target_currency_id=target_currency_id,
            start_date=start_date,
            end_date=end_date,
        )
    elif SEK_CURRENCY_ID in (base_currency_id, target_currency_id):
        fetched = _fetch_from_riksbank(
            base_currency_id=base_currency_id,
            target_currency_id=target_currency_id,
            start_date=start_date,
            end_date=end_date,
        )
    else:
        raise ValueError(
            f"Unsupported currency pair {base_currency_id}->{target_currency_id}."
        )

    expected_dates = (
        set(required_dates)
        if required_dates is not None
        else set(_business_dates_between(start_date, end_date))
    )
    fetched_dates = {record.exchange_rate_date for record in fetched}
    missing = sorted(expected_dates - fetched_dates)
    if missing:
        missing_labels = ", ".join(value.isoformat() for value in missing[:5])
        raise ValueError(
            "Exchange rate provider did not return exact-date coverage for "
            f"{_code_from_id(base_currency_id)}->{_code_from_id(target_currency_id)}. "
            f"Missing dates include: {missing_labels}"
        )
    return fetched


def _fetch_from_ecb(
    *,
    base_currency_id: int,
    target_currency_id: int,
    start_date: date,
    end_date: date,
) -> list[ExchangeRateRecord]:
    is_reversed = target_currency_id == EUR_CURRENCY_ID
    api_base = EUR_CURRENCY_ID if is_reversed else base_currency_id
    api_target = base_currency_id if is_reversed else target_currency_id
    rows = get_exchange_rates_from_european_central_bank(
        api_base,
        api_target,
        start_date.isoformat(),
        end_date.isoformat(),
    )
    return _normalize_provider_rows(
        rows=rows,
        source="EuropeanCentralBank",
        base_currency_id=base_currency_id,
        target_currency_id=target_currency_id,
        reverse_value=is_reversed,
    )


def _fetch_from_riksbank(
    *,
    base_currency_id: int,
    target_currency_id: int,
    start_date: date,
    end_date: date,
) -> list[ExchangeRateRecord]:
    if base_currency_id == SEK_CURRENCY_ID:
        rate_currency_id = target_currency_id
        is_reversed = False
    elif target_currency_id == SEK_CURRENCY_ID:
        rate_currency_id = base_currency_id
        is_reversed = True
    else:
        raise ValueError("Riksbank fetch requires SEK as base or target currency.")

    rows = get_exchange_rates_from_riksbank(
        rate_currency_id,
        start_date.isoformat(),
        end_date.isoformat(),
    )
    return _normalize_provider_rows(
        rows=rows,
        source="Riksbank",
        base_currency_id=base_currency_id,
        target_currency_id=target_currency_id,
        reverse_value=is_reversed,
    )


def _normalize_provider_rows(
    *,
    rows,
    source: str,
    base_currency_id: int,
    target_currency_id: int,
    reverse_value: bool,
) -> list[ExchangeRateRecord]:
    if isinstance(rows, str):
        raise ValueError(rows)

    normalized_rows: list[tuple[date, Decimal]] = []
    if isinstance(rows, dict) and "data" in rows:
        for item in rows["data"]:
            item_date = _coerce_date(item.get("date"))
            value = item.get("value")
            if value is None:
                continue
            normalized_rows.append((item_date, Decimal(str(value))))
    else:
        for item in rows:
            item_date = _coerce_date(item.get("date"))
            value = item.get("value")
            if value is None:
                continue
            numeric = float(value)
            if math.isnan(numeric):
                continue
            normalized_rows.append((item_date, Decimal(str(value))))

    records: list[ExchangeRateRecord] = []
    for item_date, value in normalized_rows:
        rate_value = Decimal("1") / value if reverse_value else value
        records.append(
            ExchangeRateRecord(
                exchange_rate_date=item_date,
                value=rate_value,
                source=source,
                base_currency_id=base_currency_id,
                target_currency_id=target_currency_id,
            )
        )
    return records


def _coerce_date(value) -> date:
    if isinstance(value, date):
        return value
    if value is None:
        raise ValueError("Date value is required.")
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _code_from_id(currency_id: int) -> str:
    for known_id, _, iso_code in DEFAULT_CURRENCIES:
        if known_id == currency_id:
            return iso_code
    raise ValueError(f"Unknown currency id '{currency_id}'.")
