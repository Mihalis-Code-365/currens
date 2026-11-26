"""gettext-based messages for the CLI and the legacy collector (#3).

Modules import `_` from here rather than relying on a builtin: until `setup_translation`
runs, `_` returns the English message, so importing the collector as a library no longer
fails with `NameError: _`.
"""

import gettext
from pathlib import Path

_translation: gettext.NullTranslations = gettext.NullTranslations()


def setup_translation(lang_code: str) -> None:
    """Switch messages to `lang_code` ("el" for Greek, "en" for English)."""
    global _translation
    locales_dir = Path(__file__).resolve().parents[3] / "locales"
    _translation = gettext.translation(
        "messages", localedir=locales_dir, languages=[lang_code], fallback=True
    )
    # Still installed as the builtin `_`, for any caller that relied on it.
    _translation.install()


def _(message: str) -> str:
    return _translation.gettext(message)
