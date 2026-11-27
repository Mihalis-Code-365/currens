"""The currencies currens supports: one table, the single source for ids and ISO codes.

Ids are stored in every cache, so this table is append-only: never renumber or reuse an
id. 1/2/3 are EUR/USD/SEK; routing relies on EUR and SEK, and the v1 Riksbank repair on USD.

The list is every currency both providers publish daily, checked live on 2026-11-26: the
ECB's reference rates against EUR (`EXR/D.<CUR>.EUR.SP00.A`) and the Riksbank's group 130
against SEK (`SEK<CUR>PMI`, SEK per 1 unit) cover the same 28 currencies plus EUR and SEK.
Discontinued and pre-euro currencies (BGN, HRK, RUB, DEM, ...) are out of scope (#24).
"""

from currens.errors import UnsupportedCurrencyError

CURRENCIES: tuple[tuple[int, str, str], ...] = (
    (1, "EUR", "Euro"),
    (2, "USD", "US dollar"),
    (3, "SEK", "Swedish krona"),
    (4, "AUD", "Australian dollar"),
    (5, "BRL", "Brazilian real"),
    (6, "CAD", "Canadian dollar"),
    (7, "CHF", "Swiss franc"),
    (8, "CNY", "Chinese yuan renminbi"),
    (9, "CZK", "Czech koruna"),
    (10, "DKK", "Danish krone"),
    (11, "GBP", "UK pound sterling"),
    (12, "HKD", "Hong Kong dollar"),
    (13, "HUF", "Hungarian forint"),
    (14, "IDR", "Indonesian rupiah"),
    (15, "ILS", "Israeli shekel"),
    (16, "INR", "Indian rupee"),
    (17, "ISK", "Iceland krona"),
    (18, "JPY", "Japanese yen"),
    (19, "KRW", "Korean won"),
    (20, "MXN", "Mexican peso"),
    (21, "MYR", "Malaysian ringgit"),
    (22, "NOK", "Norwegian krone"),
    (23, "NZD", "New Zealand dollar"),
    (24, "PHP", "Philippine peso"),
    (25, "PLN", "Polish zloty"),
    (26, "RON", "Romanian leu"),
    (27, "SGD", "Singapore dollar"),
    (28, "THB", "Thai baht"),
    (29, "TRY", "Turkish lira"),
    (30, "ZAR", "South African rand"),
)

_CURRENCY_IDS_TO_ISO_CODES = {currency_id: iso_code for currency_id, iso_code, _ in CURRENCIES}
_ISO_CODES_TO_CURRENCY_IDS = {iso_code: currency_id for currency_id, iso_code, _ in CURRENCIES}


def get_currency_iso_code_by_id(currency_id: int) -> str:
    try:
        return _CURRENCY_IDS_TO_ISO_CODES[currency_id]
    except KeyError as exc:
        raise UnsupportedCurrencyError(f"Unknown currency id '{currency_id}'.") from exc


def get_currency_id_by_iso_code(currency_code: str) -> int:
    try:
        return _ISO_CODES_TO_CURRENCY_IDS[currency_code.upper()]
    except KeyError as exc:
        raise UnsupportedCurrencyError(f"Unsupported currency '{currency_code}'.") from exc
