from __future__ import annotations

import gettext
import re
import string
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCALES_DIR = ROOT / "locales"
# `_("...")` calls with a single string literal, possibly spanning lines.
_CALL = re.compile(r'\b_\(\s*"((?:[^"\\]|\\.)*)"\s*\)')


def _source_msgids() -> set[str]:
    msgids: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        msgids.update(_CALL.findall(path.read_text(encoding="utf-8")))
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
