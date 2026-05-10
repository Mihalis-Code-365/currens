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

# Tests
$env:PYTHONPATH='src'
..\apps\Mihalis\backend\.venv\Scripts\python -m pytest tests/test_service.py -q

# Lint & format (ruff is the configured tool)
ruff check src/
ruff format src/
```

## Architecture

**Runtime flow:** Mihalis backend → `currens.service` → `apis/rate_sources.py` + local SQLite cache

- `src/currens/service.py` — Primary in-process API: bootstrap import from Mihalis, exact-date lookup, period prefetch, and date-specific prefetch
- `src/currens/__main__.py` — CLI entry point; supports `--init`, `--recreate`, and `--import-mihalis-rates`
- `src/currens/apis/rate_sources.py` — ECB and Riksbank fetchers
- `src/currens/db/session.py` / `src/currens/db/models.py` — legacy SQLAlchemy support used by older collector code
- `utils/multilanguage_support.py` — gettext-based i18n; `setup_translation(lang_code)` installs `_()` globally; locales in `locales/` (Greek `el`, English `en`)

**Database contract:** `currens` owns its own SQLite cache and canonical `currencies` rows. Historical Mihalis `exchange_rates` can be imported because both projects use the same currency ID mapping:

- `1 = EUR`
- `2 = USD`
- `3 = SEK`

**Behavioral notes:**

- `currens` is used as an in-process Python dependency, not an HTTP API.
- Mihalis Sweden annual reports use `ensure_rates_for_dates(...)` so provider holidays do not fail unused full-year dates.
- Exact-date lookup is still required for dates actually used by calculations.

**Package management:** Uses `uv`. Python 3.13+ required.
