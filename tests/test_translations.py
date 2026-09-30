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
