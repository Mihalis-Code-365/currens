# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Initialize / manage the local DB
python -m currens --init                 # Initialize database
python -m currens --recreate             # Drop and recreate database
python -m currens --import-mihalis-rates <path-to-trades.db>

# Tests (pytest is in the `dev` dependency group, installed by `uv sync`)
PYTHONPATH=src uv run pytest tests/test_service.py -q

# Lint & format (ruff is the configured tool)
uv run ruff check src/
uv run ruff format src/
```

## Architecture

**Runtime flow:** Mihalis backend → `currens.service` → `apis/rate_sources.py` + local SQLite cache

- `src/currens/service.py` — Primary in-process API: bootstrap import from Mihalis, exact-date lookup, period prefetch, and date-specific prefetch
- `src/currens/__main__.py` — CLI entry point; supports `--init`, `--recreate`, and `--import-mihalis-rates`
- `src/currens/apis/rate_sources.py` — ECB and Riksbank fetchers
- `src/currens/collector/core.py` — legacy collector behind the CLI: `init_db` / `recreate_db` (seeds EUR/USD/SEK currencies) and `store_riksbank_rates` / `store_european_central_bank_rates`
- `src/currens/utils/currency.py` — hardcoded currency id → ISO code map (`1=EUR`, `2=USD`, `3=SEK`) via `get_currency_iso_code_by_id`; used by `rate_sources.py` and the collector
- `src/currens/db/session.py` / `src/currens/db/models.py` — legacy SQLAlchemy support used by older collector code
- `src/currens/utils/multilanguage_support.py` — gettext-based i18n; `setup_translation(lang_code)` installs `_()` globally; locales in `locales/` (Greek `el`, English `en`)

**Database contract:** `currens` owns its own SQLite cache and canonical `currencies` rows. Historical Mihalis `exchange_rates` can be imported via `import_mihalis_rates`. The importer supports both schema versions:
- **New schema (portfolio.db v3+):** `base_currency TEXT`, `target_currency TEXT` — ISO codes imported via `_currency_id_from_code`
- **Old schema (trades.db):** `base_currency_id INT`, `target_currency_id INT` — mapping: `1=EUR`, `2=USD`, `3=SEK`

**Behavioral notes:**

- `currens` is used as an in-process Python dependency, not an HTTP API.
- Mihalis Sweden annual reports use `ensure_rates_for_dates(...)` so provider holidays do not fail unused full-year dates.
- Exact-date lookup is still required for dates actually used by calculations.

**Package management:** Uses `uv`. Python 3.13+ required.
