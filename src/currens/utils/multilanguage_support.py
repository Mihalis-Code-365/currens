import gettext
import os
from pathlib import Path


def setup_translation(lang_code):
    locales_dir = Path(__file__).resolve().parents[3] / "locales"
    lang = gettext.translation(
        "messages", localedir=locales_dir, languages=[lang_code], fallback=True
    )
    lang.install()
    # Now _() is globally available


# 🧪 Choose language: "el" for Greek, "en" for English
# setup_translation("el")  # Change to "en" to switch language
# setup_translation("en")  # Change to "en" to switch language
