from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import logging
import math
import sqlite3
from typing import Iterable

from currens.apis.rate_sources import (
    get_exchange_rates_from_european_central_bank,
    get_exchange_rates_from_riksbank,
)
from currens.errors import (
    RateNotPublishedError,
    RateProviderUnavailableError,
    UnsupportedCurrencyError,
)
from currens.utils.currency import (
    CURRENCIES,
    get_currency_id_by_iso_code,
    get_currency_iso_code_by_id,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "db" / "exchange_rates.db"
# (id, name, iso_code) rows seeded into every cache's `currencies` table.
DEFAULT_CURRENCIES = tuple((currency_id, name, iso_code) for currency_id, iso_code, name in CURRENCIES)
EUR_CURRENCY_ID = 1
USD_CURRENCY_ID = 2
SEK_CURRENCY_ID = 3
# The only ids Mihalis' old schema (trades.db) shares with currens.
_LEGACY_CURRENCY_IDS = frozenset({EUR_CURRENCY_ID, USD_CURRENCY_ID, SEK_CURRENCY_ID})

_CURRENT_DB_PATH = DEFAULT_DB_PATH


@dataclass(frozen=True, slots=True)
class ExchangeRateRecord:
    """One rate: how many units of the rate currency one unit of the base currency buys (#1)."""

    exchange_rate_date: date
    rate: Decimal
    source: str
    base_currency_id: int
    rate_currency_id: int

    # Old field names, for attribute reads only: constructing a record or calling
    # dataclasses.replace() needs the new names.
    @property
    def value(self) -> Decimal:
        return self.rate

    @property
    def target_currency_id(self) -> int:
        return self.rate_currency_id


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
        # Mihalis' v3 schema renamed three things at once: the currency columns became TEXT codes,
        # `exchange_rate_date` became `rate_date`, and `value` became `rate`. Each is sniffed on
        # its own rather than inferred from the currency columns -- assuming one rename implied
        # the others is what made this function unable to read the very schema it was extended
        # for, since it selected the new currency columns alongside the old date/value ones.
        use_text_codes = "base_currency" in col_names
        date_column = "rate_date" if "rate_date" in col_names else "exchange_rate_date"
        value_column = "rate" if "rate" in col_names else "value"
        currency_columns = (
            "base_currency, target_currency" if use_text_codes
            else "base_currency_id, target_currency_id"
        )
        # Aliased back to the legacy names so everything downstream reads one set of keys.
        rows = source_connection.execute(
            f"""
            SELECT source,
                   {date_column} AS exchange_rate_date,
                   {currency_columns},
                   {value_column} AS value
            FROM exchange_rates
            ORDER BY {date_column} ASC
            """
        ).fetchall()

    skipped: list[str] = []
    with _connect(_CURRENT_DB_PATH) as connection:
        for row in rows:
            # A pair currens cannot name is skipped like a bad rate, not allowed to abort the
            # whole import. Old-schema ids are trusted only for 1/2/3: they were rows of each
            # trades.db's own `currencies` table, so a 4 there need not be currens' 4 (AUD).
            if use_text_codes:
                base_code = str(row["base_currency"])
                rate_code = str(row["target_currency"])
                pair_label = f"{base_code}->{rate_code}"
                try:
                    base_id = _currency_id_from_code(base_code)
                    rate_id = _currency_id_from_code(rate_code)
                except UnsupportedCurrencyError:
                    skipped.append(f"{row['exchange_rate_date']} {pair_label}: unsupported currency")
                    continue
            else:
                base_id = int(row["base_currency_id"])
                rate_id = int(row["target_currency_id"])
                if not {base_id, rate_id} <= _LEGACY_CURRENCY_IDS:
                    skipped.append(
                        f"{row['exchange_rate_date']} currency ids {base_id}->{rate_id}: "
                        "old-schema ids above 3 are ambiguous"
                    )
                    continue
            # A bad rate is skipped, not stored: the missing date is then fetched from the
            # provider, whereas a stored zero would break every lookup of the reverse pair.
            try:
                rate = float(row["value"])
            except (TypeError, ValueError):
                rate = math.nan
            if not _is_usable_rate(rate):
                skipped.append(
                    f"{row['exchange_rate_date']} {_label_for_id(base_id)}->"
                    f"{_label_for_id(rate_id)} = {row['value']!r}"
                )
                continue
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
                    rate_id,
                    rate,
                ),
            )
            imported_count += 1
        _delete_invalid_riksbank_rows(connection)
        connection.commit()
    if skipped:
        _LOGGER.warning(
            "Skipped %d exchange-rate rows from %s whose rate is not a finite positive "
            "number or whose currencies are not supported; supported pairs are fetched "
            "from the provider when needed. First ones: %s",
            len(skipped),
            source_db_path,
            "; ".join(skipped[:5]),
        )
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
        rate_currency_id = _currency_id_from_code(target_currency)
        if base_currency_id == rate_currency_id:
            continue
        if _has_complete_coverage(base_currency_id, rate_currency_id, start_date, end_date):
            continue
        _fetch_and_store_pair(
            base_currency_id=base_currency_id,
            rate_currency_id=rate_currency_id,
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
        rate_currency_id = _currency_id_from_code(target_currency)
        if base_currency_id == rate_currency_id:
            continue

        missing_dates = [
            current_date
            for current_date in normalized_dates
            if _lookup_rate(base_currency_id, rate_currency_id, current_date) is None
        ]
        if not missing_dates:
            continue

        _fetch_and_store_pair(
            base_currency_id=base_currency_id,
            rate_currency_id=rate_currency_id,
            start_date=min(missing_dates),
            end_date=max(missing_dates),
            required_dates=missing_dates,
        )

        unresolved_dates = [
            current_date
            for current_date in missing_dates
            if _lookup_rate(base_currency_id, rate_currency_id, current_date) is None
        ]
        if unresolved_dates:
            missing_labels = ", ".join(value.isoformat() for value in unresolved_dates[:5])
            raise RateNotPublishedError(
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
    rate_currency_id = _currency_id_from_code(target_currency)
    if base_currency_id == rate_currency_id:
        return True
    return _lookup_rate(base_currency_id, rate_currency_id, calculation_date) is not None


def get_rate(
    calculation_date: date,
    base_currency: str,
    target_currency: str,
    *,
    db_path: str | Path | None = None,
) -> Decimal:
    configure_service_database(db_path)
    base_currency_id = _currency_id_from_code(base_currency)
    rate_currency_id = _currency_id_from_code(target_currency)
    if base_currency_id == rate_currency_id:
        return Decimal("1")

    rate = _lookup_rate(base_currency_id, rate_currency_id, calculation_date)
    if rate is not None:
        return rate

    _fetch_and_store_pair(
        base_currency_id=base_currency_id,
        rate_currency_id=rate_currency_id,
        start_date=calculation_date,
        end_date=calculation_date,
        required_dates=[calculation_date],
    )
    rate = _lookup_rate(base_currency_id, rate_currency_id, calculation_date)
    if rate is None:
        raise RateNotPublishedError(
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
    rate_currency_id = _currency_id_from_code(target_currency)

    with _connect(_CURRENT_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT exchange_rate_date, value, source, base_currency_id, target_currency_id
            FROM exchange_rates
            WHERE base_currency_id = ?
              AND target_currency_id = ?
              AND exchange_rate_date BETWEEN ? AND ?
            ORDER BY exchange_rate_date ASC, rowid ASC
            """,
            (
                base_currency_id,
                rate_currency_id,
                start_date.isoformat(),
                end_date.isoformat(),
            ),
        ).fetchall()
    # Same rule as _stored_rate: skip rows that are not usable, and return one rate per
    # date (the first usable one), since a refetch can sit next to an old bad row.
    records: list[ExchangeRateRecord] = []
    seen_dates: set[date] = set()
    for row in rows:
        row_date = _coerce_date(row["exchange_rate_date"])
        if row_date in seen_dates:
            continue
        if not isinstance(row["value"], (int, float)) or not _is_usable_rate(row["value"]):
            continue
        seen_dates.add(row_date)
        records.append(
            ExchangeRateRecord(
                exchange_rate_date=row_date,
                rate=Decimal(str(row["value"])),
                source=str(row["source"]),
                base_currency_id=int(row["base_currency_id"]),
                rate_currency_id=int(row["target_currency_id"]),
            )
        )
    return records


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
        _migrate(connection)
        connection.commit()


def _migrate(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        _delete_invalid_riksbank_rows(connection)
        connection.execute("PRAGMA user_version = 1")


def _delete_invalid_riksbank_rows(connection: sqlite3.Connection) -> None:
    """Drop Riksbank rows that are provably wrong; the cache refetches them on demand.

    - Before v1 the service fetched USD<->SEK inverted (issue #10). USD/SEK has never
      been near 1, so USD->SEK below 1 or SEK->USD above 1 is an inverted row.
    - The legacy collector stored Riksbank series without SEK in the pair (as EUR->USD).

    Runs once as a migration and again after every Mihalis import, since an older
    Mihalis database may carry rows written by the same buggy code.
    """
    connection.execute(
        """
        DELETE FROM exchange_rates
        WHERE lower(source) = 'riksbank'
          AND (
                ? NOT IN (base_currency_id, target_currency_id)
             OR (base_currency_id = ? AND target_currency_id = ? AND value < 1)
             OR (base_currency_id = ? AND target_currency_id = ? AND value > 1)
          )
        """,
        (
            SEK_CURRENCY_ID,
            USD_CURRENCY_ID, SEK_CURRENCY_ID,
            SEK_CURRENCY_ID, USD_CURRENCY_ID,
        ),
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _currency_id_from_code(currency_code: str) -> int:
    return get_currency_id_by_iso_code(currency_code)


def _lookup_rate(base_currency_id: int, rate_currency_id: int, calculation_date: date) -> Decimal | None:
    with _connect(_CURRENT_DB_PATH) as connection:
        direct = _stored_rate(connection, base_currency_id, rate_currency_id, calculation_date)
        if direct is not None:
            return direct
        inverse = _stored_rate(connection, rate_currency_id, base_currency_id, calculation_date)
        if inverse is not None:
            return Decimal("1") / inverse
    return None


def _stored_rate(
    connection: sqlite3.Connection,
    base_currency_id: int,
    rate_currency_id: int,
    calculation_date: date,
) -> Decimal | None:
    # A stored row that is not finite and positive (written before #19/#20, or by hand) is
    # treated as missing, so it is refetched rather than served or divided by.
    rows = connection.execute(
        """
        SELECT value
        FROM exchange_rates
        WHERE base_currency_id = ?
          AND target_currency_id = ?
          AND exchange_rate_date = ?
        ORDER BY rowid
        """,
        (base_currency_id, rate_currency_id, calculation_date.isoformat()),
    ).fetchall()
    for row in rows:
        if isinstance(row["value"], (int, float)) and _is_usable_rate(row["value"]):
            return Decimal(str(row["value"]))
    return None


def _has_complete_coverage(
    base_currency_id: int,
    rate_currency_id: int,
    start_date: date,
    end_date: date,
) -> bool:
    required_dates = _business_dates_between(start_date, end_date)
    if not required_dates:
        return True
    return all(
        _lookup_rate(base_currency_id, rate_currency_id, current_date) is not None
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
    rate_currency_id: int,
    start_date: date,
    end_date: date,
    required_dates: Iterable[date] | None,
) -> None:
    fetched_records = _fetch_pair_from_provider(
        base_currency_id=base_currency_id,
        rate_currency_id=rate_currency_id,
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
                    record.rate_currency_id,
                    float(record.rate),
                )
                for record in fetched_records
            ],
        )
        connection.commit()


def _fetch_pair_from_provider(
    *,
    base_currency_id: int,
    rate_currency_id: int,
    start_date: date,
    end_date: date,
    required_dates: Iterable[date] | None,
) -> list[ExchangeRateRecord]:
    if EUR_CURRENCY_ID in (base_currency_id, rate_currency_id):
        fetched = _fetch_from_ecb(
            base_currency_id=base_currency_id,
            rate_currency_id=rate_currency_id,
            start_date=start_date,
            end_date=end_date,
        )
    elif SEK_CURRENCY_ID in (base_currency_id, rate_currency_id):
        fetched = _fetch_from_riksbank(
            base_currency_id=base_currency_id,
            rate_currency_id=rate_currency_id,
            start_date=start_date,
            end_date=end_date,
        )
    else:
        # No provider publishes a pair without EUR or SEK, and a cross rate would be a
        # derived figure, not an official one; for tax use that is the caller's choice (#24).
        raise UnsupportedCurrencyError(
            f"No provider publishes {_code_from_id(base_currency_id)}->"
            f"{_code_from_id(rate_currency_id)}: one side must be EUR (ECB) or SEK "
            "(Riksbank). Convert through EUR or SEK explicitly."
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
        raise RateNotPublishedError(
            "Exchange rate provider did not return exact-date coverage for "
            f"{_code_from_id(base_currency_id)}->{_code_from_id(rate_currency_id)}. "
            f"Missing dates include: {missing_labels}"
        )
    return fetched


def _fetch_from_ecb(
    *,
    base_currency_id: int,
    rate_currency_id: int,
    start_date: date,
    end_date: date,
) -> list[ExchangeRateRecord]:
    is_reversed = rate_currency_id == EUR_CURRENCY_ID
    api_base_currency_id = EUR_CURRENCY_ID if is_reversed else base_currency_id
    api_rate_currency_id = base_currency_id if is_reversed else rate_currency_id
    rows = get_exchange_rates_from_european_central_bank(
        api_base_currency_id,
        api_rate_currency_id,
        start_date.isoformat(),
        end_date.isoformat(),
    )
    return _normalize_provider_rows(
        rows=rows,
        source="EuropeanCentralBank",
        base_currency_id=base_currency_id,
        rate_currency_id=rate_currency_id,
        invert_rate=is_reversed,
    )


def _fetch_from_riksbank(
    *,
    base_currency_id: int,
    rate_currency_id: int,
    start_date: date,
    end_date: date,
) -> list[ExchangeRateRecord]:
    # Riksbank series (e.g. SEKUSDPMI) quote SEK per 1 unit of the foreign currency,
    # so they are already X->SEK and only SEK->X needs the reciprocal.
    if base_currency_id == SEK_CURRENCY_ID:
        foreign_currency_id = rate_currency_id
        is_reversed = True
    elif rate_currency_id == SEK_CURRENCY_ID:
        foreign_currency_id = base_currency_id
        is_reversed = False
    else:
        raise ValueError("Riksbank fetch requires SEK as the base or rate currency.")

    rows = get_exchange_rates_from_riksbank(
        foreign_currency_id,
        start_date.isoformat(),
        end_date.isoformat(),
    )
    return _normalize_provider_rows(
        rows=rows,
        source="Riksbank",
        base_currency_id=base_currency_id,
        rate_currency_id=rate_currency_id,
        invert_rate=is_reversed,
    )


def _normalize_provider_rows(
    *,
    rows,
    source: str,
    base_currency_id: int,
    rate_currency_id: int,
    invert_rate: bool,
) -> list[ExchangeRateRecord]:
    is_data_envelope = isinstance(rows, dict) and "data" in rows
    if is_data_envelope:
        rows = rows["data"]
    # Anything but a list is a malformed answer; iterating a dict would read as "not published".
    if not isinstance(rows, list):
        raise RateProviderUnavailableError(
            f"Unexpected response from {source}: expected a list of rows, got {rows!r}"
        )
    records: list[ExchangeRateRecord] = []
    rate_by_date: dict[date, Decimal] = {}
    for item in rows:
        # A bad row must raise, never be skipped: a skipped row reads as "not published".
        try:
            item_date = _coerce_date(item.get("date"))
            raw_value = item.get("value")
            if raw_value is None:
                continue
            if not is_data_envelope and math.isnan(float(raw_value)):
                continue
            provider_rate = Decimal(str(raw_value))
            rate = Decimal("1") / provider_rate if invert_rate else provider_rate
        except (ValueError, ArithmeticError, AttributeError, TypeError) as exc:
            raise RateProviderUnavailableError(
                f"Unreadable row from {source}: {item!r} ({exc})"
            ) from exc
        # Checked before anything is cached: a zero would later divide by zero on the reverse pair.
        if not _is_usable_rate(rate):
            raise RateProviderUnavailableError(
                f"Rate from {source} is not a finite positive number: {item!r}"
            )
        # The cache keeps one row per date, so a second, different rate would silently win.
        if item_date in rate_by_date:
            if rate_by_date[item_date] != rate:
                raise RateProviderUnavailableError(
                    f"{source} returned conflicting rates for {item_date.isoformat()}: "
                    f"{rate_by_date[item_date]} and {rate}"
                )
            continue
        rate_by_date[item_date] = rate
        records.append(
            ExchangeRateRecord(
                exchange_rate_date=item_date,
                rate=rate,
                source=source,
                base_currency_id=base_currency_id,
                rate_currency_id=rate_currency_id,
            )
        )
    return records


def _is_usable_rate(value: Decimal | float) -> bool:
    """A rate can be stored, served and inverted only if it is finite and positive.

    Judged as the float the cache stores, so a Decimal that rounds to 0.0 or overflows to
    infinity is refused before it is cached, not silently served as missing afterwards.
    """
    if isinstance(value, Decimal):
        if not value.is_finite():  # also covers sNaN, which float() refuses to convert
            return False
        value = float(value)
    return math.isfinite(value) and value > 0


def _coerce_date(value) -> date:
    if isinstance(value, date):
        return value
    if value is None:
        raise ValueError("Date value is required.")
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _label_for_id(currency_id: int) -> str:
    """The ISO code for a log line, or the raw id: an unknown id must not turn a skip into an abort."""
    try:
        return _code_from_id(currency_id)
    except ValueError:
        return f"currency id {currency_id}"


def _code_from_id(currency_id: int) -> str:
    return get_currency_iso_code_by_id(currency_id)
