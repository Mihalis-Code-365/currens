# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Initialize / manage the local DB
python -m currens --init                 # Initialize database
python -m currens --recreate             # Drop and recreate database

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

**Runtime flow:** calling application → `currens.service` → `apis/rate_sources.py` + local SQLite cache. currens is a standalone ECB/Riksbank rate cache; it contains no code for any particular app

- `src/currens/service.py` — Primary in-process API: `store_rates` (load a caller's own rows, all or nothing), exact-date lookup, period prefetch, and date-specific prefetch. `get_rate_record` returns the rate with its provenance (`series_id`, `fetched_at` in UTC, `inverted`, `origin`; #23, schema `user_version` 2). Rows cached earlier keep NULLs. `provider_for(base, target)` returns `"ECB"` or `"Riksbank"` with no network or cache access (#33); it raises `UnsupportedCurrencyError` for an unknown code or a pair without EUR/SEK, and a plain `ValueError` for a same-currency pair (no provider serves it). It wraps `_provider_route`, the only copy of the routing rule, which `_require_provider_route` and the fetch dispatch also use
- `src/currens/__main__.py` — CLI entry point; supports `--init` and `--recreate`
- `src/currens/apis/rate_sources.py` — ECB and Riksbank fetchers
- `src/currens/collector/core.py` — legacy collector behind the CLI: `init_db` / `recreate_db` (seeds the `CURRENCIES` table) and `store_riksbank_rates` / `store_european_central_bank_rates`
- `src/currens/utils/currency.py` — `CURRENCIES`, the one append-only currency table (ids 1–30, stored in every cache, so never renumbered or reused; routing relies on `1=EUR` and `3=SEK`). It lists every currency both the ECB and the Riksbank publish daily. A pair needs EUR (→ ECB) or SEK (→ Riksbank) on one side; any other pair raises `UnsupportedCurrencyError` (no derived cross rates, #24)
- `src/currens/db/session.py` / `src/currens/db/models.py` — legacy SQLAlchemy support used by older collector code
- `src/currens/utils/multilanguage_support.py` — gettext-based i18n for CLI/collector messages: modules `from currens.utils.multilanguage_support import _` (English until `setup_translation(lang_code)` switches it; it also still installs the builtin). Locales in `locales/` (Greek `el`, English `en`). Library exception messages stay English on purpose

**Database contract:** `currens` owns its own SQLite cache and canonical `currencies` rows. A caller loads rates it already holds with `store_rates`; everything else is fetched from the provider on demand. Opening a cache older than v1 runs `_delete_invalid_riksbank_rows` once, a cache-integrity migration of Riksbank rows currens' own pre-#10 service fetched inverted and the legacy collector stored without SEK in the pair.

**Behavioral notes:**

- Terminology (#1): for a pair base->rate, the *rate* is how many units of the *rate currency* one unit of the *base currency* buys. Internal names use `rate` / `rate_currency_id`; the public keyword `target_currency` and the DB columns `target_currency_id` / `value` keep their old names for compatibility.

- `currens` is used as an in-process Python dependency, not an HTTP API.
- A caller that needs only some dates of a period should use `ensure_rates_for_dates(...)`, so provider holidays do not fail dates it never uses.
- Exact-date lookup is still required for dates actually used by calculations.
- Riksbank gaps (#22): when a Riksbank-routed pair (SEK with a non-EUR currency) lacks a weekday from yesterday back `RIKSBANK_GAP_WINDOW_DAYS` (7), the SWEA `CalendarDays` endpoint is asked about those dates only. A bank day, or a failed calendar call, gives `RateProviderUnavailableError`. Every other missing date (a holiday, a weekend, today or later, or anything older) stays `RateNotPublishedError` without a calendar call.

**Package management:** Uses `uv`. Python 3.13+ required.
