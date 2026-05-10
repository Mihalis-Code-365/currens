# currens

`currens` is the exchange-rate service/cache used by Mihalis. It owns provider fetches, local FX caching, exact-date lookup, and bootstrap import of historical exchange-rate rows from the Mihalis backend database.

## What It Owns

- ECB and Riksbank exchange-rate fetching
- local SQLite FX cache storage
- exact-date rate lookup for `EUR`, `USD`, and `SEK`
- period prefetch and date-specific prefetch
- one-time bootstrap import from Mihalis historical `exchange_rates`
- in-process Python API consumed by Mihalis

## Public Service API

- `import_mihalis_rates(source_db_path, currens_db_path=None)`
- `ensure_rates(pairs, start_date, end_date, db_path=None)`
- `ensure_rates_for_dates(pairs, required_dates, db_path=None)`
- `get_rate(date, base_currency, target_currency, db_path=None)`
- `has_rate(date, base_currency, target_currency, db_path=None)`
- `get_rates_for_period(base_currency, target_currency, start_date, end_date, db_path=None)`

Currencies at the API boundary use ISO codes: `EUR`, `USD`, `SEK`.

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

When used from Mihalis, the backend points `currens` at:

```text
apps/Mihalis/backend/storage/db/currens_exchange_rates.db
```

## CLI Commands

Initialize the local schema:

```bash
python -m currens --init
```

Drop and recreate the local schema:

```bash
python -m currens --recreate
```

Bootstrap historical FX rows from the Mihalis backend database:

```bash
python -m currens --import-mihalis-rates d:\Python\Mihalis_workspace\apps\Mihalis\backend\storage\db\trades.db
```

The bootstrap import copies only `exchange_rates` rows. `currens` keeps its own canonical `currencies` table and relies on the shared ID mapping:

- `1 = EUR`
- `2 = USD`
- `3 = SEK`

## Notes

- `currens` is used in-process by Mihalis; it is not an HTTP service.
- Sweden annual reports in Mihalis prefetch only the dates actually needed by the report, not the full calendar year.
- Exact-date lookup is still enforced for dates the report actually uses.

## Testing

```bash
$env:PYTHONPATH='src'
..\apps\Mihalis\backend\.venv\Scripts\python -m pytest tests/test_service.py -q
```
