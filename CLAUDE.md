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

# Translations: after adding/changing a _("...") message, refresh the catalogs,
# fill in the new msgstr entries (el, en), then recompile. tests/test_translations.py
# fails if a compiled catalog is missing a source message.
pybabel extract --project=currens --version=0.1.0 -o locales/messages.pot src/currens
pybabel update -i locales/messages.pot -d locales -D messages --no-fuzzy-matching --ignore-obsolete
pybabel compile -d locales -D messages --statistics

# Lint & format (ruff is the configured tool)
uv run ruff check src/
uv run ruff format src/
```

## Architecture

**Runtime flow:** Mihalis backend → `currens.service` → `apis/rate_sources.py` + local SQLite cache

- `src/currens/service.py` — Primary in-process API: bootstrap import from Mihalis, exact-date lookup, period prefetch, and date-specific prefetch
- `src/currens/__main__.py` — CLI entry point; supports `--init`, `--recreate`, and `--import-mihalis-rates`
- `src/currens/apis/rate_sources.py` — ECB and Riksbank fetchers
- `src/currens/collector/core.py` — legacy collector behind the CLI: `init_db` / `recreate_db` (seeds the `CURRENCIES` table) and `store_riksbank_rates` / `store_european_central_bank_rates`
- `src/currens/utils/currency.py` — `CURRENCIES`, the one append-only currency table (ids 1–30; `1=EUR`, `2=USD`, `3=SEK` are fixed for Mihalis' old schema). It lists every currency both the ECB and the Riksbank publish daily. A pair needs EUR (→ ECB) or SEK (→ Riksbank) on one side; any other pair raises `UnsupportedCurrencyError` (no derived cross rates, #24)
- `src/currens/db/session.py` / `src/currens/db/models.py` — legacy SQLAlchemy support used by older collector code
- `src/currens/utils/multilanguage_support.py` — gettext-based i18n; `setup_translation(lang_code)` installs `_()` globally; locales in `locales/` (Greek `el`, English `en`)

**Database contract:** `currens` owns its own SQLite cache and canonical `currencies` rows. Historical Mihalis `exchange_rates` can be imported via `import_mihalis_rates`. The importer supports both schema versions:
- **New schema (portfolio.db v3+):** `base_currency TEXT`, `target_currency TEXT` — ISO codes imported via `_currency_id_from_code`
- **Old schema (trades.db):** `base_currency_id INT`, `target_currency_id INT` — mapping: `1=EUR`, `2=USD`, `3=SEK`

**Behavioral notes:**

- Terminology (#1): for a pair base->rate, the *rate* is how many units of the *rate currency* one unit of the *base currency* buys. Internal names use `rate` / `rate_currency_id`; the public keyword `target_currency` and the DB columns `target_currency_id` / `value` keep their old names for compatibility.

- `currens` is used as an in-process Python dependency, not an HTTP API.
- Mihalis Sweden annual reports use `ensure_rates_for_dates(...)` so provider holidays do not fail unused full-year dates.
- Exact-date lookup is still required for dates actually used by calculations.

**Package management:** Uses `uv`. Python 3.13+ required.
