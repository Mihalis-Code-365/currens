import gettext
import os


def setup_translation(lang_code):
    locales_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "locales")
    lang = gettext.translation(
        "messages", localedir=locales_dir, languages=[lang_code], fallback=True
    )
    lang.install()
    # Now _() is globally available


# 🧪 Choose language: "el" for Greek, "en" for English
# setup_translation("el")  # Change to "en" to switch language
# setup_translation("en")  # Change to "en" to switch language
