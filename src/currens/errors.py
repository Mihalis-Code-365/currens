"""
Exceptions raised by currens when an exchange rate cannot be provided.

Callers that walk back to an earlier business day should do so only on
`RateNotPublishedError`; `RateProviderUnavailableError` means the answer is
unknown, not that there is no rate.
"""


class RateNotPublishedError(ValueError):
    """The provider answered successfully but publishes no rate for the date.

    Subclasses `ValueError` so existing broad `except ValueError` handlers keep working.
    """


class RateProviderUnavailableError(Exception):
    """The provider could not be asked, or its answer could not be understood.

    Covers network failures, timeouts, HTTP errors and unparseable responses.
    Deliberately not a `ValueError`: it must not be mistaken for missing data.
    """


class UnsupportedCurrencyError(ValueError):
    """currens cannot provide this currency or pair at all, on any date.

    Raised for an unknown ISO code or id, and for a pair with neither EUR nor SEK in it:
    no provider publishes such a pair, and currens does not derive cross rates (#24).
    Not a `RateNotPublishedError`, so a caller walking back day by day stops at once.
    """
