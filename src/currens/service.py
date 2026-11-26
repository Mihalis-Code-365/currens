from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
import logging
import math
import sqlite3
from typing import Iterable, Iterator, Literal
from zoneinfo import ZoneInfo

from currens.apis.rate_sources import (
    get_exchange_rates_from_european_central_bank,
    get_exchange_rates_from_riksbank,
    get_swedish_bank_days,
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
# How far back a Riksbank bank day without a rate counts as "maybe late", not "final" (#22).
RIKSBANK_GAP_WINDOW_DAYS = 7
_LEGACY_CURRENCY_IDS = frozenset({EUR_CURRENCY_ID, USD_CURRENCY_ID, SEK_CURRENCY_ID})

_CURRENT_DB_PATH = DEFAULT_DB_PATH

RateProvider = Literal["ECB", "Riksbank"]


@dataclass(frozen=True, slots=True)
class ExchangeRateRecord:
    """One rate: how many units of the rate currency one unit of the base currency buys (#1)."""

    exchange_rate_date: date
    rate: Decimal
    source: str
    base_currency_id: int
    rate_currency_id: int
    # Provenance (#23); None for rows cached before it was recorded.
    series_id: str | None = None
    """The provider's series, e.g. `SEKUSDPMI` or `EXR/D.USD.EUR.SP00.A`."""
    fetched_at: datetime | None = None
    """When currens fetched the rate from the provider (UTC)."""
    inverted: bool | None = None
    """True if `rate` is the reciprocal of the provider's own quote."""
    origin: str | None = None
    """`provider` for a fetch, `import_mihalis_rates:<file>` for an imported row."""

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


@contextmanager
def using_service_database(db_path: str | Path) -> Iterator[Path]:
    """Point currens at `db_path` for the block, then point it back (#36).

    Yields the resolved path. On exit, normal or by exception, the previous path is restored
    by assignment only: the previous file is never created, migrated or touched. Blocks nest.
    """
    global _CURRENT_DB_PATH
    previous = _CURRENT_DB_PATH
    try:
        configure_service_database(db_path)
        yield _CURRENT_DB_PATH
    finally:
        _CURRENT_DB_PATH = previous


def store_rates(
    rates: Iterable[tuple[date, str, str, Decimal]],
    *,
    source: str,
    origin: str,
    currens_db_path: str | Path | None = None,
) -> int:
    """Load caller-supplied rates into the cache: `(date, base_code, rate_code, rate)` rows (#36).

    Every row is checked before anything is written, so one bad row writes nothing; the error
    names its date and pair (`2024-01-02 USD->SEK`). Refused: a date that is not a plain `date`
    (a `datetime` would be stored as a timestamp no lookup matches) and a rate that is not a
    `Decimal` (`TypeError`); an unknown code or a pair with neither EUR nor SEK
    (`UnsupportedCurrencyError`); a same-currency pair, which currens answers as 1 without a row,
    and a rate that is not finite and positive (`ValueError`). Codes are case-insensitive. An
    empty `source` or `origin` is a `ValueError` naming it. Returns the number of rows.

    A row for the same date, pair and `source` is replaced in place: its value and `origin` are
    updated, its provenance (series, fetch time, direction) cleared, and it keeps its position.
    A row from another `source` is never replaced, and a lookup reads the oldest row for a date
    and pair, so a rate stored here does not win over one already cached from another source.
    """
    configure_service_database(currens_db_path)
    for name, value in (("source", source), ("origin", origin)):
        if not value.strip():
            raise ValueError(f"store_rates needs a non-empty {name}.")
    checked = [_checked_rate_row(row) for row in rates]
    with _connect(_CURRENT_DB_PATH) as connection:
        connection.executemany(
            """
            INSERT INTO exchange_rates (
                source, exchange_rate_date, base_currency_id, target_currency_id, value,
                series_id, fetched_at, inverted, origin
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
            ON CONFLICT (source, exchange_rate_date, base_currency_id, target_currency_id)
            DO UPDATE SET
                value = excluded.value,
                series_id = NULL,
                fetched_at = NULL,
                inverted = NULL,
                origin = excluded.origin
            """,
            [
                (source, when.isoformat(), base_id, rate_id, float(rate), origin)
                for when, base_id, rate_id, rate in checked
            ],
        )
        connection.commit()
    return len(checked)


def _checked_rate_row(
    row: tuple[date, str, str, Decimal],
) -> tuple[date, int, int, Decimal]:
    when, base_code, rate_code, rate = row
    label = f"{when} {str(base_code).upper()}->{str(rate_code).upper()}"
    if isinstance(when, datetime) or not isinstance(when, date):
        raise TypeError(f"{label}: the date must be a date, not {type(when).__name__}.")
    if not isinstance(rate, Decimal):
        raise TypeError(f"{label}: the rate must be a Decimal, not {type(rate).__name__}.")
    try:
        base_id = _currency_id_from_code(base_code)
        rate_id = _currency_id_from_code(rate_code)
        if base_id != rate_id:
            _require_provider_route(base_id, rate_id)
    except UnsupportedCurrencyError as exc:
        raise UnsupportedCurrencyError(f"{label}: {exc}") from exc
    if base_id == rate_id:
        raise ValueError(f"{label}: a same-currency pair is never stored; its rate is 1.")
    if not _is_usable_rate(rate):
        raise ValueError(f"{label}: rate {rate} is not finite and positive.")
    return when, base_id, rate_id, rate


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
    # Series, fetch time and direction are not in Mihalis' table, so they stay NULL (#23).
    import_origin = f"import_mihalis_rates:{Path(source_db_path).expanduser().resolve()}"
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
                    value,
                    origin
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row["source"] or "legacy"),
                    _coerce_date(row["exchange_rate_date"]).isoformat(),
                    base_id,
                    rate_id,
                    rate,
                    import_origin,
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
        _require_provider_route(base_currency_id, rate_currency_id)
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
        _require_provider_route(base_currency_id, rate_currency_id)

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
    if not _has_provider_route(base_currency_id, rate_currency_id):
        return False
    return _lookup_rate(base_currency_id, rate_currency_id, calculation_date) is not None


def provider_for(base_currency: str, target_currency: str) -> RateProvider:
    """Which provider currens asks for the pair: "ECB" or "Riksbank" (#33).

    The ECB when EUR is on either side (EUR/SEK included), else the Riksbank when SEK is.
    It reads no cache and makes no network call. Codes are case-insensitive, as in every
    other public function.

    Raises `UnsupportedCurrencyError` for an unknown code (checked first, so XYZ/XYZ raises
    it too) and for a pair with neither EUR nor SEK. Raises a plain `ValueError` for a
    same-currency pair (EUR/EUR, GBP/GBP): currens supports it, but no provider serves it,
    since `get_rate` answers 1 without asking one. `UnsupportedCurrencyError` is itself a
    `ValueError`, so a caller telling the two apart catches `UnsupportedCurrencyError` first.
    """
    base_currency_id = _currency_id_from_code(base_currency)
    rate_currency_id = _currency_id_from_code(target_currency)
    if base_currency_id == rate_currency_id:
        raise ValueError(
            f"{_code_from_id(base_currency_id)}->{_code_from_id(rate_currency_id)} is the same "
            "currency: no provider serves it, "
            "its rate is 1."
        )
    return _require_provider_route(base_currency_id, rate_currency_id)


def get_rate_record(
    calculation_date: date,
    base_currency: str,
    target_currency: str,
    *,
    db_path: str | Path | None = None,
) -> ExchangeRateRecord:
    """Like `get_rate`, but the whole record, so a caller can show where the rate came from (#23).

    `record.rate` equals what `get_rate` returns. The provenance fields are None for rows
    cached before they were recorded, and for the identity rate of a currency to itself.
    """
    configure_service_database(db_path)
    base_currency_id = _currency_id_from_code(base_currency)
    rate_currency_id = _currency_id_from_code(target_currency)
    if base_currency_id == rate_currency_id:
        return ExchangeRateRecord(
            exchange_rate_date=calculation_date,
            rate=Decimal("1"),
            source="identity",
            base_currency_id=base_currency_id,
            rate_currency_id=rate_currency_id,
        )
    _require_provider_route(base_currency_id, rate_currency_id)
    record = _lookup_record(base_currency_id, rate_currency_id, calculation_date)
    if record is None:
        get_rate(calculation_date, base_currency, target_currency, db_path=db_path)
        record = _lookup_record(base_currency_id, rate_currency_id, calculation_date)
    if record is None:  # get_rate raises first; kept so the return type holds
        raise RateNotPublishedError(
            f"Exchange rate not found for {base_currency} to {target_currency} on "
            f"{calculation_date.isoformat()}"
        )
    return record


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
    _require_provider_route(base_currency_id, rate_currency_id)

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
            f"""
            SELECT {_RECORD_COLUMNS}
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
        records.append(_record_from_row(row))
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
    # Nullable provenance columns (#23); rows cached before keep NULLs. Checked on every
    # open, not gated on user_version: the collector's --recreate drops and recreates the
    # table without them while the version in the file header stays at 2.
    existing = {row[1] for row in connection.execute("PRAGMA table_info(exchange_rates)")}
    for column, sql_type in _PROVENANCE_COLUMNS:
        if column not in existing:
            connection.execute(f"ALTER TABLE exchange_rates ADD COLUMN {column} {sql_type}")
    if version < 2:
        connection.execute("PRAGMA user_version = 2")


_PROVENANCE_COLUMNS = (
    ("series_id", "TEXT"),
    ("fetched_at", "TEXT"),
    ("inverted", "INTEGER"),
    ("origin", "TEXT"),
)


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
    record = _lookup_record(base_currency_id, rate_currency_id, calculation_date)
    return None if record is None else record.rate


def _lookup_record(
    base_currency_id: int, rate_currency_id: int, calculation_date: date
) -> ExchangeRateRecord | None:
    with _connect(_CURRENT_DB_PATH) as connection:
        direct = _stored_record(connection, base_currency_id, rate_currency_id, calculation_date)
        if direct is not None:
            return direct
        inverse = _stored_record(connection, rate_currency_id, base_currency_id, calculation_date)
        if inverse is not None:
            # Served as the reciprocal of the stored row, so relative to the provider's own
            # quote it is inverted exactly when the stored row was not.
            return replace(
                inverse,
                rate=Decimal("1") / inverse.rate,
                base_currency_id=base_currency_id,
                rate_currency_id=rate_currency_id,
                inverted=None if inverse.inverted is None else not inverse.inverted,
            )
    return None


def _stored_record(
    connection: sqlite3.Connection,
    base_currency_id: int,
    rate_currency_id: int,
    calculation_date: date,
) -> ExchangeRateRecord | None:
    # A stored row that is not finite and positive (written before #19/#20, or by hand) is
    # treated as missing, so it is refetched rather than served or divided by.
    rows = connection.execute(
        f"""
        SELECT {_RECORD_COLUMNS}
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
            return _record_from_row(row)
    return None


_RECORD_COLUMNS = (
    "exchange_rate_date, value, source, base_currency_id, target_currency_id,"
    " series_id, fetched_at, inverted, origin"
)


def _record_from_row(row: sqlite3.Row) -> ExchangeRateRecord:
    return ExchangeRateRecord(
        exchange_rate_date=_coerce_date(row["exchange_rate_date"]),
        rate=Decimal(str(row["value"])),
        source=str(row["source"]),
        base_currency_id=int(row["base_currency_id"]),
        rate_currency_id=int(row["target_currency_id"]),
        series_id=_text_or_none(row["series_id"]),
        fetched_at=_utc_datetime_or_none(row["fetched_at"]),
        inverted=row["inverted"] if row["inverted"] is None else bool(row["inverted"]),
        origin=_text_or_none(row["origin"]),
    )


# Provenance is informational: an unreadable value (hand-edited, another tool) reads as
# unknown, so it can never make get_rate fail where it served the rate before #23.
def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _utc_datetime_or_none(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


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
                value,
                series_id,
                fetched_at,
                inverted,
                origin
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record.source,
                    record.exchange_rate_date.isoformat(),
                    record.base_currency_id,
                    record.rate_currency_id,
                    float(record.rate),
                    record.series_id,
                    None if record.fetched_at is None else record.fetched_at.isoformat(),
                    None if record.inverted is None else int(record.inverted),
                    record.origin,
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
    provider = _require_provider_route(base_currency_id, rate_currency_id)
    if provider == "ECB":
        fetched = _fetch_from_ecb(
            base_currency_id=base_currency_id,
            rate_currency_id=rate_currency_id,
            start_date=start_date,
            end_date=end_date,
        )
    else:
        fetched = _fetch_from_riksbank(
            base_currency_id=base_currency_id,
            rate_currency_id=rate_currency_id,
            start_date=start_date,
            end_date=end_date,
        )

    expected_dates = (
        set(required_dates)
        if required_dates is not None
        else set(_business_dates_between(start_date, end_date))
    )
    fetched_dates = {record.exchange_rate_date for record in fetched}
    missing = sorted(expected_dates - fetched_dates)
    if missing and provider == "Riksbank":
        _check_riksbank_gaps(missing, base_currency_id, rate_currency_id)
    if missing:
        missing_labels = ", ".join(value.isoformat() for value in missing[:5])
        raise RateNotPublishedError(
            "Exchange rate provider did not return exact-date coverage for "
            f"{_code_from_id(base_currency_id)}->{_code_from_id(rate_currency_id)}. "
            f"Missing dates include: {missing_labels}"
        )
    return fetched


def _provider_route(
    base_currency_id: int, rate_currency_id: int
) -> RateProvider | None:
    """The routing rule, the only copy (#33): EUR on either side is the ECB (EUR/SEK too),
    else SEK on either side is the Riksbank, else None (no provider publishes the pair)."""
    pair = (base_currency_id, rate_currency_id)
    if EUR_CURRENCY_ID in pair:
        return "ECB"
    if SEK_CURRENCY_ID in pair:
        return "Riksbank"
    return None


def _has_provider_route(base_currency_id: int, rate_currency_id: int) -> bool:
    return _provider_route(base_currency_id, rate_currency_id) is not None


def _require_provider_route(
    base_currency_id: int, rate_currency_id: int
) -> RateProvider:
    """Refuse a pair with neither EUR nor SEK, before any cache or coverage check (#24, #31).

    No provider publishes such a pair, and a cross rate would be a derived figure, not an
    official one; for tax use that is the caller's choice. Checking first keeps a cached row
    (e.g. imported from Mihalis) or a range without business dates from slipping past.
    """
    provider = _provider_route(base_currency_id, rate_currency_id)
    if provider is None:
        raise UnsupportedCurrencyError(
            f"No provider publishes {_code_from_id(base_currency_id)}->"
            f"{_code_from_id(rate_currency_id)}: one side must be EUR (ECB) or SEK "
            "(Riksbank). Convert through EUR or SEK explicitly."
        )
    return provider


def _check_riksbank_gaps(missing: list[date], base_currency_id: int, rate_currency_id: int) -> None:
    """Raise RateProviderUnavailableError if a missing Riksbank date may still get a rate (#22).

    An empty Riksbank answer reads as "not published", which lets a caller walk back a day.
    That is right for a Swedish holiday, but a recent bank day without a rate may be a late
    publication or an outage. Only weekdays from yesterday back RIKSBANK_GAP_WINDOW_DAYS are
    in question, and only for those is the Riksbank calendar asked (one call, that range):
    - a bank day: RateProviderUnavailableError, the answer is unknown;
    - a holiday: not published, as before;
    - calendar unreachable or unreadable: RateProviderUnavailableError, never a guess.
    Every other date stays "not published" without a calendar call, so a calendar failure
    cannot change it: today or later (the rate appears in the afternoon), weekends, and
    older dates, where a gap is final (11 years of history show it is rare, e.g. SEKEURPMI
    on 2022-02-02).
    """
    today = _stockholm_today()
    in_window = [
        missing_date
        for missing_date in missing
        if missing_date.weekday() < 5
        and 1 <= (today - missing_date).days <= RIKSBANK_GAP_WINDOW_DAYS
    ]
    if not in_window:
        return
    days = get_swedish_bank_days(min(in_window).isoformat(), max(in_window).isoformat())
    pair = f"{_code_from_id(base_currency_id)}->{_code_from_id(rate_currency_id)}"
    for missing_date in in_window:
        is_bank_day = days.get(missing_date.isoformat())
        if is_bank_day is None:
            raise RateProviderUnavailableError(
                f"Riksbank calendar has no entry for {missing_date.isoformat()}"
            )
        if is_bank_day:
            raise RateProviderUnavailableError(
                f"Riksbank returned no {pair} rate for {missing_date.isoformat()}, a Swedish "
                "bank day: it may be published late, so the rate is unknown, not absent."
            )


def _stockholm_today() -> date:
    return datetime.now(ZoneInfo("Europe/Stockholm")).date()


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
        series_id=(
            f"EXR/D.{_code_from_id(api_rate_currency_id)}."
            f"{_code_from_id(api_base_currency_id)}.SP00.A"
        ),
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
        series_id=f"SEK{_code_from_id(foreign_currency_id)}PMI",
    )


def _normalize_provider_rows(
    *,
    rows,
    source: str,
    base_currency_id: int,
    rate_currency_id: int,
    invert_rate: bool,
    series_id: str | None = None,
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
    fetched_at = datetime.now(UTC)
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
                series_id=series_id,
                fetched_at=fetched_at,
                inverted=invert_rate,
                origin="provider",
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
