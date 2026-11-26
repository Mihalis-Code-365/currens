# currens independent of Mihalis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** currens contains no code that exists for Mihalis (or any other app), and the old-data repairs move out of the library into one-time scripts that live in Mihalis.

**Architecture:** Four ordered deliveries, each mergeable on its own:
**A** (currens PR, additive) adds an app-agnostic `store_rates` and a public test seam `using_service_database`.
**B** (Mihalis PR) stops relying on the importer: tests and the seeder load rates through `store_rates`, `conftest.py` uses the seam, a one-time handover script settles the live cache, and the startup bootstrap is deleted.
**C** (currens PR, breaking 0.x) deletes the importer, its CLI flag and the legacy-id path. The v0→v1 Riksbank-row repair **stays**: it fixes rates currens' own pre-#10 service fetched inverted, so it is the library maintaining its own cache; only its post-import call goes.
**D** (Mihalis PR) bumps the pin to C.

**Tech Stack:** Python 3.13, sqlite3, pytest, uv, ruff, pybabel. currens repo `Mihalis-Code-365/currens`; Mihalis repo `Nikolaos-Paximadakis/Mihalis` (backend under `backend/`).

**Spec:** currens issue #36 (https://github.com/Mihalis-Code-365/currens/issues/36).

## Global Constraints

- Merge commits only; every PR gets a fresh review before merge; after each currens merge, bump the Mihalis pin (`backend/pyproject.toml`, `currens = { git = ..., rev = "<full sha>" }`).
- Mihalis work happens in a worktree made with `scripts/setup_worktree.sh`, never in the main checkout.
- Library exception messages stay English. A removed `_("...")` message means refreshing the catalogs (commands in currens `CLAUDE.md`); `tests/test_translations.py` must pass.
- No bare `# type: ignore` / blanket `noqa` / `Any`; an ignore names its rule and gives a reason.
- The cache's id table stays append-only (ids 1–30, never renumbered); `EUR_CURRENCY_ID = 1` and `SEK_CURRENCY_ID = 3` stay — routing uses them.
- Rows already in caches with `origin = 'import_mihalis_rates:<file>'` are data and stay untouched.
- Out of scope: the legacy collector / SQLAlchemy `db/` package, the default cache location, dropping Mihalis' own `exchange_rates` table from `portfolio.db`, PyPI.
- Live data (`backend/storage/db/portfolio.db`, the FX cache named by `MIHALIS_FX_CACHE_DB_PATH` in `backend/.env`) is changed only by Task B3's `--apply`, and only after the user approves the `--check` output. Nothing in this plan deletes a row from the live cache.
- Same-currency pairs are never stored: currens serves them as `Decimal("1")` without a cache row.

## Review Focus

1. **A `datetime` passed as the date to `store_rates`** — `datetime` is a `date` subclass, and `isoformat()` would store `2024-01-02T00:00:00`, which no lookup ever matches. Expect `TypeError`. Test in A1.
2. **A batch with one bad row** (unknown code, cross pair, same-currency pair, rate 0/NaN/inf) — expect nothing written and an error naming the row's date and pair. Test in A1.
3. **`store_rates` into a cache that already holds a row for the same date and pair under another `source`** — lookups take the oldest row (`ORDER BY rowid`), so the stored row does not win, and replacing an existing row must not move it in that order (`INSERT OR REPLACE` would give it a new rowid). Expect this documented in the docstring and pinned by tests. Test in A1.
4. **`using_service_database` exiting by exception, or nested** — expect the previous path restored in both cases, and the previous file never created or touched on exit. Test in A2.
5. **The sample seeder run beside real data** (`--output … --skip-statements` with `backend/.env` naming the live FX cache) — expect it to refuse rather than write fictional rates into a cache it does not own. Test in B2.
6. **Golden tax reports after the seeder change** — the stored FX values must be the same floats as before. After `golden_tax_reports.py update`, expect only `tests/golden/tax_reports.seeder-sha256` to change (the seeder file's hash); any figure diff means the values moved. Verified in B2.

---

## Delivery A — currens: app-agnostic loading and test seam (one PR)

Branch: `feat/36-store-rates` from currens `main`.

### Task A1: `store_rates`

**Files:**
- Modify: `src/currens/service.py` (new function after `configure_service_database`; reuse `_require_provider_route`, `_is_usable_rate`, `_currency_id_from_code`, `_connect`)
- Modify: `src/currens/__init__.py` (export)
- Test: `tests/test_store_rates.py` (new)

**Interfaces:**
- Produces:
  ```python
  def store_rates(
      rates: Iterable[tuple[date, str, str, Decimal]],   # (date, base_code, rate_code, rate)
      *,
      source: str,
      origin: str,
      currens_db_path: str | Path | None = None,
  ) -> int
  ```
  Calls `configure_service_database(currens_db_path)` first (same contract as the other entry points). Validates every row before writing anything; then one transaction of `INSERT INTO exchange_rates (...) VALUES (...) ON CONFLICT(source, exchange_rate_date, base_currency_id, target_currency_id) DO UPDATE SET value = excluded.value, series_id = NULL, fetched_at = NULL, inverted = NULL, origin = excluded.origin`, which keeps an existing row's rowid. The rate is bound as `float(rate)`, as the provider path does. Returns the number of input rows. Codes are case-insensitive. Errors: `UnsupportedCurrencyError` for an unknown code or a pair without EUR/SEK; `ValueError` for a same-currency pair or a rate that `_is_usable_rate` refuses; `TypeError` when the date is a `datetime` or not a `date`. Each of these names the row's date and pair (`2024-01-02 USD->SEK`). An empty/whitespace `source` or `origin` is a `ValueError` naming that argument. Docstring states point 3 of Review Focus.

- [ ] **Step 1: Write the failing tests** in `tests/test_store_rates.py`, each on a `tmp_path` cache:
  - `test_stored_rate_is_served_without_a_fetch` — store `(date(2024,1,2), "usd", "SEK", Decimal("10.5"))`, then with `rate_sources.urlopen` monkeypatched to raise, `get_rate(date(2024,1,2), "USD", target_currency="SEK") == Decimal("10.5")` and `get_rate_record(...).origin == "fixture"`, `series_id is None`.
  - `test_returns_the_number_of_rows` — 3 rows → `3`.
  - `test_one_bad_row_writes_nothing` — parametrized over `("XXX","SEK")`, `("USD","NOK")`, `("SEK","SEK")`, rate `Decimal(0)`, `Decimal("NaN")`, `Decimal("Infinity")`, placed second of three rows: the matching exception type is raised, the message contains `2024-01-03`, and the cache has 0 `exchange_rates` rows.
  - `test_datetime_is_refused` — `datetime(2024,1,2)` as date → `TypeError`, 0 rows.
  - `test_empty_source_or_origin_is_refused` — `source=" "` and `origin=""` each → `ValueError` whose message contains `source` / `origin` respectively.
  - `test_existing_row_from_another_source_still_wins` — insert an `ECB` row for EUR->USD 2024-01-02 = 1.1 via `store_rates(..., source="ECB", origin="provider")`, then `store_rates` the same date/pair = 2.0 with `source="fixture"`; `get_rate` returns `Decimal("1.1")`.
  - `test_same_source_replaces` — storing the same date/pair/source twice serves the second value.
  - `test_replacing_a_row_keeps_its_place` — store EUR->USD 2024-01-02 under `source="A"` = 1.1, then under `"B"` = 1.2, then `"A"` again = 1.3; `get_rate` returns `Decimal("1.3")` (A is still the oldest row).

- [ ] **Step 2: Run** `PYTHONPATH=src uv run pytest tests/test_store_rates.py -q` — expect FAIL (`ImportError: cannot import name 'store_rates'`).
- [ ] **Step 3: Implement** `store_rates` as specified above and add it to `currens.__all__`.
- [ ] **Step 4: Run** `PYTHONPATH=src uv run pytest -q` — expect all PASS; `uv run ruff check src/` clean.
- [ ] **Step 5: Commit** `feat: store_rates loads caller-supplied rates into the cache (#36)`.

### Task A2: `using_service_database`

**Files:**
- Modify: `src/currens/service.py`, `src/currens/__init__.py`
- Test: `tests/test_store_rates.py` (same file; it is the "pointing currens at a cache" test module)
- Modify: `README.md` (document `store_rates` and `using_service_database` under the API list)

**Interfaces:**
- Produces:
  ```python
  @contextmanager
  def using_service_database(db_path: str | Path) -> Iterator[Path]
  ```
  On enter: remembers the current path, calls `configure_service_database(db_path)`, yields the resolved path. On exit (normal or exception): restores the remembered path by assignment only — no `mkdir`, no schema work on the restored path. Export from `currens`.

- [ ] **Step 1: Write the failing tests:**
  - `test_using_service_database_points_and_restores` — inside the block, `store_rates` with no `currens_db_path` writes to the block's file; after it, `currens.service._CURRENT_DB_PATH` equals the value before (reading the private name is fine in currens' own tests).
  - `test_restores_after_an_exception` — raise inside the block; path restored.
  - `test_nested_blocks_restore_in_order` — outer A, inner B; after inner exit the path is A, after outer exit the original.
  - `test_exit_does_not_create_the_previous_file` — set the current path (via monkeypatch) to `tmp_path/"never"/"c.db"`, enter and leave a block on another file; `tmp_path/"never"` does not exist.
- [ ] **Step 2: Run** the four tests — expect FAIL (import error).
- [ ] **Step 3: Implement** and export; add the README lines.
- [ ] **Step 4: Run** `PYTHONPATH=src uv run pytest -q`, `uv run ruff check src/` — expect PASS / clean.
- [ ] **Step 5: Commit** `feat: using_service_database, a public seam for pointing currens at a cache (#36)`.

**Then:** open the PR (`Part of #36`), fresh review, merge (merge commit). Note the merge sha for B.

---

## Delivery B — Mihalis: stop relying on the importer (one PR)

Branch in a new worktree: `chore/currens-36-independence`. First commit: bump the currens pin to Delivery A's merge sha (`backend/pyproject.toml`; update the comment block above it that explains the pin), `uv lock`, full backend test run green.

### Task B1: tests seed the FX cache, not `portfolio.db`

**Files:**
- Modify: `backend/tests/conftest.py` — add a helper and switch the cache-restore patch
- Modify: `backend/tests/test_shared_fixtures.py` (~108–132) — it reads `currens_service._CURRENT_DB_PATH` to prove the restore; rewrite it to prove the same through the public seam (after the fixture, a `store_rates` without `currens_db_path` does not land in the deleted scratch file), or delete the private-path check if the seam's own currens tests already cover it
- Modify: every test that inserts into `portfolio.db`'s `exchange_rates` to get a rate: `backend/tests/test_calculations.py` (lines ~653, ~699, ~1428, ~3322, ~5516, ~7351 and `_use_test_db` ~1136), `test_cross_broker_average_cost.py:139`, `test_export_contents.py` (~209, ~298), `test_fx_rates.py` (~34), `test_greek_dividend_classification.py:1148`, `test_tender_offer_sell_rows.py:355`. Re-run `git grep -n "INSERT INTO exchange_rates" backend/tests` to catch any added since this plan.
- Leave alone: `backend/tests/fixtures/schema_v3_baseline.sql` and the schema in `test_calculations.py:212` (the table still exists in `portfolio.db`; dropping it is out of scope).

**Interfaces:**
- Consumes: `currens.store_rates`, `currens.using_service_database` (A1, A2).
- Produces (in `backend/tests/conftest.py`):
  ```python
  def seed_fx_rates(rows: Iterable[tuple[date | str, str, str, float | Decimal | str]], *, source: str = "fixture") -> int
  ```
  Converts a `str` date with `date.fromisoformat` and a non-Decimal rate with `Decimal(str(value))`, then calls `currens.store_rates(..., source=source, origin="mihalis-test", currens_db_path=fx_rates.CURRENT_FX_CACHE_DB_PATH)` directly (`test_fx_cache_guard.py` scans only `services/fx_rates.py`, so no `_fx_cache()` wrapper).
  The `isolated_fx_cache` fixture becomes a generator annotated `Iterator[Path | None]`: the `real_fx_cache` marker branch does `yield None` then `return`; the normal branch replaces `monkeypatch.setattr(currens.service, "_CURRENT_DB_PATH", currens.service._CURRENT_DB_PATH)` with `with currens.using_service_database(scratch): yield scratch`.

- [ ] **Step 1:** Add `seed_fx_rates` and the fixture change; run `cd backend && uv run pytest -q` — expect the same pass count as the pin-bump run (nothing uses the helper yet).
- [ ] **Step 2:** Convert each call site to `seed_fx_rates(...)` with the same rows, **minus same-currency rows** (e.g. the `EUR→EUR` row in `test_calculations.py` ~653), which `store_rates` refuses and currens never needed; add one assertion in that module that `fx_rates.get_rate(<a fixture date>, "EUR", "EUR") == Decimal("1")`. `_use_test_db(path)` in `test_calculations.py` stops calling `import_historical_rates`; where the test DB carries FX rows those tests need, seed them with `seed_fx_rates` at the call site instead. `test_export_contents.py:298` likewise.
- [ ] **Step 3:** Delete or rewrite the tests whose subject is the bootstrap itself (in `test_fx_rates.py`, and `test_calculations.py:8939 test_calculate_stock_position_uses_currens_cache_not_backend_exchange_rates`, whose point — the cache wins over `portfolio.db` — becomes "`portfolio.db` rows are never read": keep it in that form, asserting a rate only in `portfolio.db` raises the missing-rate error).
- [ ] **Step 4:** `cd backend && uv run pytest -q` — expect PASS, and `git grep -n "import_historical_rates\|INSERT INTO exchange_rates" backend/tests` shows only the schema fixtures and the rewritten test from Step 3.
- [ ] **Step 5: Commit** `test: seed FX through currens.store_rates, not portfolio.db (currens#36)`.

### Task B2: the seeder writes FX into the FX cache

**Files:**
- Modify: `backend/scripts/seed_sample_db.py` — `_seed_fx` (~1548) and its caller (~2799)
- Modify: `backend/scripts/golden_tax_reports.py` module docstring (the "FX comes from the seeder's frozen series" bullet)

**Interfaces:**
- Consumes: `currens.store_rates`.
- Change: `_seed_fx` builds the same `FX_SERIES` rows and calls `currens.store_rates(rows, source=<series source>, origin="mihalis-seed", currens_db_path=<seed FX cache>)`, grouping by source since `source` is per call. Rates go in as `Decimal(repr(value))` so the stored float is bit-identical to today's. It no longer writes `portfolio.db.exchange_rates`. Keep the returned row count.
- **Seed FX cache = a file the seeder owns, never the configured one by default.** New option `--fx-cache PATH`; default `<output db's directory>/currens_exchange_rates.db` (the golden runner already names exactly that path via `MIHALIS_FX_CACHE_DB_PATH`, so it keeps working). Before writing, refuse with a clear message if the file exists and holds any `exchange_rates` row whose `origin` is not `mihalis-seed`. This check runs for `--skip-statements` too (today's safety checks return early for it, `seed_sample_db.py` ~1250–1269).

- [ ] **Step 1:** From `backend/`, run `uv run python scripts/golden_tax_reports.py check` before the change and record that it passes.
- [ ] **Step 2:** Add to `backend/tests/test_seed_sample_db.py`: `test_seed_fx_refuses_a_cache_it_does_not_own` (a `--fx-cache` file holding one `origin="provider"` row → refusal, file unchanged, also with `--skip-statements`), `test_seed_fx_defaults_beside_the_output_db`, and `test_seed_fx_reseeds_its_own_cache`. Run — expect FAIL.
- [ ] **Step 2b:** Make the change; update the golden docstring bullet to say the seeder writes the frozen series straight into the scratch FX cache. Run the new tests — expect PASS.
- [ ] **Step 3:** `check` now fails with "regenerate the goldens", because `tests/golden/tax_reports.seeder-sha256` hashes the whole seeder file. Run `uv run python scripts/golden_tax_reports.py update`. Expect `git status` to show **only** `tests/golden/tax_reports.seeder-sha256` changed. Any other golden file changing means the FX values moved: stop and fix that, don't commit it. Then `check` passes, and so do `tests/test_golden_tax_reports.py` and the seeder's own tests (`git grep -ln seed_sample_db backend/tests`).
- [ ] **Step 4: Commit** `chore: seeder loads its frozen FX series into the FX cache (currens#36)`.

### Task B3: one-time handover script for the live cache

**Files:**
- Create: `backend/scripts/one_off/currens_36_fx_handover.py`
- Test: `backend/tests/test_currens_36_fx_handover.py`

**Interfaces:**
- Consumes: `currens.store_rates`, `currens.UnsupportedCurrencyError`.
- Produces: CLI `uv run python scripts/one_off/currens_36_fx_handover.py [--apply] [--portfolio-db PATH] [--fx-cache PATH]` (defaults from `core.config`). Read-only unless `--apply`, and it never deletes or overwrites a cache row. Prints a report with the cache's `PRAGMA user_version` and these classes of `portfolio.db` `exchange_rates` rows (v3 columns only: `source, rate_date, base_currency, target_currency, rate`), keyed by `(source, date, base, target)`:
  - **identical** — in the cache with the same stored float: count only.
  - **conflicting** — in the cache with a different value: each listed with key, both values, and the cache row's `origin`. Never changed. If any exist, `--apply` refuses and exits non-zero; the user decides what to do with them by hand.
  - **missing** — not in the cache: listed. With `--apply` (and no conflicts) copied via one `store_rates` call per source with `origin="import_mihalis_rates:<resolved portfolio.db path>"` (the origin string the old importer wrote, so provenance stays uniform).
  - **skipped** — unknown code, no EUR/SEK, same currency, or a rate `_is_usable_rate` would refuse: listed, never copied.
  - **would-have-been-repaired** — missing rows matching the old Riksbank repair predicate (USD->SEK < 1, SEK->USD > 1, or a Riksbank pair without SEK): moved from "missing" to this list and not copied, since the old importer deleted them right after inserting them.
  Module docstring: what it is for, that it is run once and then kept only as history, link to currens#36.

- [ ] **Step 1: Write failing tests** on `tmp_path` DBs (portfolio.db from `tests/fixtures/schema_v3_baseline.sql`, cache from `currens.using_service_database`):
  - `test_check_writes_nothing` — one missing, one identical, one conflicting row; without `--apply` both files are byte-identical afterwards and the report counts 1/1/1.
  - `test_apply_refuses_while_conflicts_exist` — same fixture with `--apply`: non-zero exit, cache unchanged.
  - `test_apply_copies_missing_rows_with_the_import_origin` — no conflicts; after `--apply`, `currens.get_rate_record(...)` on the copied row has `origin == f"import_mihalis_rates:{path}"`, and the identical row's `origin` is unchanged.
  - `test_unusable_rows_are_skipped_and_listed` — rows `USD->NOK`, `XXX->EUR`, `EUR->EUR`, rate `0` are in the skipped list and absent from the cache.
  - `test_inverted_riksbank_rows_are_not_copied` — a missing Riksbank USD->SEK = 0.1 row is listed as would-have-been-repaired and absent from the cache after `--apply`.
  - `test_second_apply_is_a_no_op` — after a successful `--apply`, the next run reports 0 missing.
- [ ] **Step 2:** Run — expect FAIL (module missing).
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the test file and the full backend suite — expect PASS.
- [ ] **Step 5: Commit** `chore: one-time FX handover script for currens#36`.
- [ ] **Step 6 (user gate):** run it **without** `--apply` against the live files; show the user the full report. Run `--apply` only on their explicit OK, after `uv run python scripts/backup_storage.py`. Record the before/after counts in the PR description.

> **HARD STOP before B4.** Continue only when a `--check` run against the live files reports **0 missing and 0 conflicting**, either straight away or after an approved `--apply`. Otherwise stop and hand the report to the user. B4 must not be committed and the B PR must not be opened before then.

### Task B4: delete the bootstrap

**Files:**
- Modify: `backend/services/fx_rates.py` — delete `import_historical_rates`, `_bootstrap_if_needed`, `_BOOTSTRAPPED_SOURCE_DBS`, the `_ImportsMihalisRates` protocol, and the `_bootstrap_if_needed(service)` call in `_configured_service`; drop `_either_database` from this module if nothing else uses it.
- Modify: `backend/tests/conftest.py` — remove `monkeypatch.setattr(fx_rates, "_BOOTSTRAPPED_SOURCE_DBS", set())`.
- Modify: `backend/tests/test_fx_cache_guard.py` — remove `import_mihalis_rates` from `EITHER_DATABASE_CALLS` and the two expected-count entries (~413–414); if the either-database rule then guards nothing, delete that rule and its self-tests rather than leave a guard with no subject.
- Modify: Mihalis `CLAUDE.md` (~68–76, the "One-time bootstrap" block → point to the handover script as history), `backend/pyproject.toml` pin comment, `ARCHITECTURE.md`/`docs` if `git grep -n "import_mihalis_rates\|bootstrap" -- '*.md'` finds live (non-archive, non-CHANGELOG) mentions.

- [ ] **Step 1:** Delete the code and the guard entries.
- [ ] **Step 2:** `cd backend && uv run pytest -q` and `uv run python scripts/golden_tax_reports.py check` — expect PASS; `uv run ruff check .` and the project's type check clean.
- [ ] **Step 3:** `git grep -n "import_mihalis_rates" -- backend ':!backend/scripts/one_off' ':!*CHANGELOG*'` — expect no hits.
- [ ] **Step 4: Commit** `refactor: drop the currens import bootstrap; the FX cache is the only FX store (currens#36)`.

**Then:** PR in Mihalis (`Part of Mihalis-Code-365/currens#36`), fresh review, merge.

---

## Delivery C — currens: delete the Mihalis-specific code (one PR, breaking)

Branch: `refactor/36-drop-mihalis-importer` from currens `main`. Start only after B is merged on Mihalis `main` (otherwise Mihalis' pin bump in D breaks).

### Task C1: remove the importer and the legacy-id path

**Files:**
- Modify: `src/currens/service.py` — delete `import_mihalis_rates` (which takes its post-import `_delete_invalid_riksbank_rows` call with it), `_LEGACY_CURRENCY_IDS` and its comment. **Keep** `_delete_invalid_riksbank_rows`, its `version < 1` call in `_migrate`, and `USD_CURRENCY_ID`; reword the docstring so it explains a cache-integrity migration of rows written by currens' own pre-#10 service and the legacy collector, with no Mihalis or import wording. Reword the `ExchangeRateRecord.origin` docstring (`provider` for a fetch, the caller's `origin` for `store_rates`) and the `_require_provider_route` docstring ("e.g. imported from Mihalis" → "e.g. loaded with `store_rates`"). Delete `_label_for_id` and `_coerce_date` if nothing else uses them.
- Modify: `src/currens/__init__.py` — remove the export.
- Modify: `src/currens/__main__.py` — remove the `--import-mihalis-rates` branch and import.
- Modify: `src/currens/utils/currency.py` docstring — ids are append-only because every cache stores them; no Mihalis reference.
- Modify: `locales/` — refresh catalogs (the two removed messages become obsolete and drop out with `--ignore-obsolete`), recompile.
- Modify: `README.md`, `CLAUDE.md` — remove the importer, the "Database contract" schema-version section and the Mihalis framing; describe currens as a standalone ECB/Riksbank rate cache.
- Tests:
  - Delete (subject was the importer): `test_service.py` `test_import_mihalis_rates_bootstraps_currens_db`, `..._deduplicates_on_currens_primary_key`, `..._reads_portfolio_v3_schema`, `..._drops_invalid_riksbank_rows`; `test_currencies.py` `test_old_schema_import_skips_ids_it_cannot_trust`, `test_new_schema_import_skips_unsupported_codes_instead_of_aborting`; `test_cache_integrity.py` `test_import_skips_and_reports_rates_that_are_not_finite_and_positive`, `test_import_still_accepts_numeric_strings_and_ints`, `test_bad_row_with_unknown_currency_id_is_skipped_not_fatal`; `test_provenance.py` `test_imported_rows_name_the_file_and_leave_the_rest_unknown`.
  - Rewrite to seed with `store_rates` or direct SQL (subject is something else): `test_cache_integrity.py` `test_repeated_identical_row_is_accepted`, `test_date_skipped_on_import_is_fetched_from_the_provider` (→ a date absent from the cache is fetched), `test_provenance.py` `test_unreadable_provenance_reads_as_unknown_and_never_blocks_the_rate`, `test_currencies.py` `test_unknown_code_in_a_cross_pair_reports_the_unknown_code`. Re-check with `grep -n import_mihalis_rates tests/` — must end empty.
  - Keep `test_service.py` `test_schema_migration_drops_only_wrong_riksbank_rows` unchanged. It pins the migration that stays.
  - Reword the comment in `tests/test_currencies.py` (~248–250) that names Mihalis.

- [ ] **Step 1:** Make the source changes; delete/rewrite the tests listed.
- [ ] **Step 2:** Refresh and compile the catalogs.
- [ ] **Step 3:** `PYTHONPATH=src uv run pytest -q` (all PASS, including `test_translations.py` and the kept migration test), `uv run ruff check src/` clean, and `grep -rni "mihalis\|trades\.db\|portfolio\.db" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=docs` returns no hits.
- [ ] **Step 4: Commit** `refactor!: remove import_mihalis_rates and the legacy-id path (closes #36)`.

**Then:** PR (`Closes #36`, body flags the breaking removal), fresh review, merge.

---

## Delivery D — Mihalis: pin bump to C

- [ ] New Mihalis issue + worktree; bump the pin to C's merge sha, update the pin comment, `uv lock`.
- [ ] `cd backend && uv run pytest -q`, golden check, ruff, type check — expect PASS. Any failure means B missed a call site; fix it in this PR.
- [ ] PR, review, merge.
