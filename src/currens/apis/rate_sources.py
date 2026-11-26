"""
This module contains functions to fetch data from external APIs.
"""

from datetime import datetime
from http.client import HTTPException
import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from currens.errors import RateProviderUnavailableError
from currens.utils.currency import get_currency_iso_code_by_id


def get_exchange_rates_from_riksbank(
    rate_currency_id: int, start_date: str, end_date: str = None
):
    """
    Get exchange rates from the Riksbank
    :param currency_id: ID of the currency
    :param start_date: Start date for fetching exchange rates
    :return: List of dicts with date and value; empty when nothing is published
    :raises RateProviderUnavailableError: if the Riksbank could not be reached
    """
    symbol = get_currency_iso_code_by_id(rate_currency_id)
    symbol_map = {"EUR": "SEKEURPMI", "USD": "SEKUSDPMI"}

    symbol_id = symbol_map.get(symbol)
    if not symbol_id:
        raise ValueError(f"Unknown symbol '{symbol}'")

    url = f"https://api.riksbank.se/swea/v1/Observations/{symbol_id}/{start_date}"
    if end_date:
        url += f"/{end_date}"
    data = _load_json(url)
    # 204 No Content (e.g. a Riksbank holiday) means no observations.
    return [] if data is None else data


def get_exchange_rates_from_european_central_bank(
    base_currency_id: int,
    rate_currency_id: int,
    start_date: str,
    end_date: str = None,
):
    """
    Get exchange rates from the European Central Bank using SDMX-JSON
    :param base_currency_id: ID of the base currency (e.g., EUR)
    :param currency_id: ID of the target currency (e.g., SEK)
    :param start_date: Start date in YYYY-MM-DD
    :param end_date: Optional end date, defaults to today
    :return: List of dicts with date and exchange rate value; empty when nothing is published
    :raises RateProviderUnavailableError: if the ECB could not be reached
    """
    base_currency: str = get_currency_iso_code_by_id(base_currency_id)
    currency: str = get_currency_iso_code_by_id(rate_currency_id)
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    # API building blocks
    entrypoint = "https://data-api.ecb.europa.eu/service/"  # "https://data.ecb.europa.eu/sdmx/2.1/"
    resource = "data"
    flow_ref = "EXR"
    key = f"D.{currency}.{base_currency}.SP00.A"  # Direction is always: target.base
    request_url = f"{entrypoint}{resource}/{flow_ref}/{key}"

    # Parameters
    parameters = {
        "startPeriod": start_date,
        "endPeriod": end_date,
        "format": "jsondata",
        "detail": "dataonly",
    }
    query_string = urlencode(parameters)
    data = _load_json(f"{request_url}?{query_string}")
    # The ECB answers 200 with an empty body for days without a fixing.
    if data is None:
        return []

    try:
        # Navigate to the "observations" key
        observations = data["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]

        # Navigate to the "TIME_PERIOD" values for the dates
        time_periods = data["structure"]["dimensions"]["observation"][0]["values"]

        dates = [time["id"] for time in time_periods]
        # An observation's key is the index of its period, so pair them by key, never by
        # position, and refuse a series where the two do not line up one to one.
        if set(observations) != {str(index) for index in range(len(dates))}:
            raise RateProviderUnavailableError(
                f"ECB observations for {key} do not line up with its periods: "
                f"keys {sorted(observations)} for {len(dates)} periods"
            )
        date_value_pairs = []
        for index, day in enumerate(dates):
            observation = observations[str(index)]
            if not isinstance(observation, list) or not observation:
                raise RateProviderUnavailableError(
                    f"Unexpected ECB observation for {key} on {day}: {observation!r}"
                )
            date_value_pairs.append({"date": day, "value": observation[0]})
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise RateProviderUnavailableError(
            f"Unexpected ECB response shape for {key}: {exc!r}"
        ) from exc

    return date_value_pairs


def _load_json(url: str):
    """Fetch and decode a JSON response.

    Returns None when the provider answers successfully with no content (HTTP 204, or an
    empty body), which both the Riksbank and the ECB use for dates without a published rate.
    """
    try:
        with urlopen(url, timeout=10) as response:
            status = response.status
            body = response.read()
    except HTTPError as exc:
        raise RateProviderUnavailableError(
            f"Rate provider returned HTTP {exc.code} for {url}"
        ) from exc
    # URLError, timeouts and dropped connections are all OSError subclasses.
    except (OSError, HTTPException) as exc:
        raise RateProviderUnavailableError(
            f"Unable to reach rate provider at {url}: {exc}"
        ) from exc

    if status == 204 or not body.strip():
        return None
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise RateProviderUnavailableError(
            f"Rate provider returned an unparseable response for {url}"
        ) from exc
    # Neither provider sends a JSON null for "no rate" (that is 204 or an empty body), and
    # returning None here would make it read as one.
    if data is None:
        raise RateProviderUnavailableError(
            f"Rate provider returned a JSON null for {url}"
        )
    return data
