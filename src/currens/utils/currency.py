_CURRENCY_IDS_TO_ISO_CODES = {
    1: "EUR",
    2: "USD",
    3: "SEK",
}


def get_currency_iso_code_by_id(currency_id: int) -> str:
    try:
        return _CURRENCY_IDS_TO_ISO_CODES[currency_id]
    except KeyError as exc:
        raise ValueError(
            f"Currency with id '{currency_id}' not found or missing iso_code."
        ) from exc
