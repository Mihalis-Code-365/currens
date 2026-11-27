# currens

`currens` is a standalone exchange-rate cache for official ECB and Riksbank rates. It owns provider fetches, local FX caching and exact-date lookup, and is used in-process as a Python library.

## What It Owns

- ECB and Riksbank exchange-rate fetching
- local SQLite FX cache storage
- exact-date rate lookup for any pair with `EUR` or `SEK` on one side (the 30 currencies in `currens.utils.currency`)
- period prefetch and date-specific prefetch
- loading rates a caller already holds (`store_rates`)
- an in-process Python API

## Public Service API

- `ensure_rates(pairs, start_date, end_date, db_path=None)`
- `ensure_rates_for_dates(pairs, required_dates, db_path=None)`
- `get_rate(date, base_currency, target_currency, db_path=None)`
- `has_rate(date, base_currency, target_currency, db_path=None)`
- `get_rates_for_period(base_currency, target_currency, start_date, end_date, db_path=None)`
- `store_rates(rates, source=..., origin=..., currens_db_path=None)`: load your own
  `(date, base_code, rate_code, Decimal)` rows into the cache, all or nothing. A row for the same
  date, pair and source is replaced in place; a row cached from another source keeps winning,
  and a row whose reverse pair is already cached for that date is refused.
- `using_service_database(db_path)`: a context manager that points currens at a cache for the
  block and restores the previous path afterwards, without touching that file (a test seam).

Currencies at the API boundary use ISO codes, e.g. `EUR`, `USD`, `SEK`.

## Installation

```bash
cd currens
uv venv
uv sync
```

## Database

By default, `currens` stores its cache in:

```text
currens/src/currens/db/exchange_rates.db
```

An application points `currens` at its own cache with `currens.service.configure_service_database(path)`, or per call
(`db_path=` on lookups, `currens_db_path=` on `store_rates`).

## CLI Commands

Initialize the local schema:

```bash
python -m currens --init
```

Drop and recreate the local schema:

```bash
python -m currens --recreate
```

`currens` keeps its own canonical `currencies` table. Its ids are stored in every cache, so the
table is append-only: an id is never renumbered or reused (`1 = EUR`, `2 = USD`, `3 = SEK`).

## Notes

- `currens` is used in-process; it is not an HTTP service.
- A caller that needs only some dates of a period can prefetch just those with
  `ensure_rates_for_dates`, so provider holidays do not fail dates it never uses.
- Exact-date lookup is still enforced for every date actually asked for.

## Testing

```bash
uv sync
PYTHONPATH=src uv run pytest -q
```
