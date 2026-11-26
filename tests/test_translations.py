from __future__ import annotations

import ast
import gettext
import string
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCALES_DIR = ROOT / "locales"
def _source_msgids() -> set[str]:
    # Parse rather than regex-match, so any quoting style and implicitly concatenated
    # literals are found, matching what pybabel extracts.
    msgids: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                msgids.add(node.args[0].value)
    return msgids


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field}


def test_source_has_translatable_messages() -> None:
    assert _source_msgids()


@pytest.mark.parametrize("lang", ["el", "en"])
def test_compiled_catalog_covers_every_source_message(lang) -> None:
    # gettext silently falls back to the English msgid when the catalog is stale,
    # so an edited message would go untranslated without this check.
    catalog = gettext.translation("messages", localedir=LOCALES_DIR, languages=[lang])
    for msgid in sorted(_source_msgids()):
        assert msgid in catalog._catalog, f"{lang}: missing translation for {msgid!r}"
        translated = catalog.gettext(msgid)
        assert _placeholders(translated) == _placeholders(msgid), f"{lang}: {msgid!r}"


def test_collector_works_without_setup_translation() -> None:
    # Before #3 the collector used a builtin `_` that only the CLI installed.
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "from currens.collector import core; print(core._('today'))"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "today"


def test_setup_translation_switches_collector_messages_to_greek(capsys) -> None:
    from currens.collector import core
    from currens.utils.multilanguage_support import setup_translation

    try:
        setup_translation("el")
        print(core._("Error fetching data: {error}").format(error="x"))
    finally:
        setup_translation("en")

    assert capsys.readouterr().out.strip() == "Σφάλμα κατά την ανάκτηση δεδομένων: x"
