"""
This module contains functions to fetch data from external APIs.
"""

from datetime import datetime
from typing import Optional

import requests

from utils.currency import get_currency_iso_code_by_id


def count_calls(func):
    def wrapper(*args, **kwargs):
        wrapper.calls += 1
        return func(*args, **kwargs)

    wrapper.calls = 0
    return wrapper


def get_exchange_rates_from_riksbank(
    currency_id: int, start_date: str, end_date: Optional[str] = None
):
    """
    Get exchange rates from the Riksbank
    :param currency_id: ID of the currency
    :param start_date: Start date for fetching exchange rates
    :return: Exchange rate data or error message
    """
    # TODO implement end_date
    try:
        symbol = get_currency_iso_code_by_id(currency_id)
        symbol_map = {"EUR": "SEKEURPMI", "USD": "SEKUSDPMI"}

        symbol_id = symbol_map.get(symbol)
        if not symbol_id:
            raise ValueError(f"Unknown symbol '{symbol}'")

        url = f"https://api.riksbank.se/swea/v1/Observations/{symbol_id}/{start_date}"
        if end_date:
            url += f"/{end_date}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # Raise an HTTPError for bad responses

        return response.json()
    except requests.exceptions.RequestException as e:
        return f"Error: Unable to fetch data. {str(e)}"
    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: An unexpected error occurred. {str(e)}"


def get_exchange_rates_from_european_central_bank(
    base_currency_id: int,
    currency_id: int,
    start_date: str,
    end_date: Optional[str] = None,
):
    """
    Get exchange rates from the European Central Bank using SDMX-JSON
    :param base_currency_id: ID of the base currency (e.g., EUR)
    :param currency_id: ID of the target currency (e.g., SEK)
    :param start_date: Start date in YYYY-MM-DD
    :param end_date: Optional end date, defaults to today
    :return: List of dicts with date and exchange rate value
    """
    base_currency: str = get_currency_iso_code_by_id(base_currency_id)
    currency: str = get_currency_iso_code_by_id(currency_id)
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
    # Request
    response = requests.get(request_url, params=parameters, timeout=10)
    if response.status_code != 200:
        raise ValueError(f"Error fetching data. Status code: {response.status_code}")

    data = response.json()

    # Navigate to the "observations" key
    observations = data["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]

    # Navigate to the "TIME_PERIOD" values for the dates
    time_periods = data["structure"]["dimensions"]["observation"][0]["values"]

    # Extract the dates and values
    dates = [time["id"] for time in time_periods]
    values = [obs[0] for obs in observations.values()]

    # Combine dates and values into a list of tuples
    date_value_pairs = [
        {"date": date, "value": value} for date, value in zip(dates, values)
    ]

    return date_value_pairs


@count_calls
def get_exchange_rate_from_exchangerate(date, base_currency, target_currency):
    if base_currency == target_currency:
        raise ValueError("base_currency is equal to target_currency")
    elif base_currency == "EUR" and target_currency == "SEK":
        return 10.0
    elif base_currency == "USD" and target_currency == "SEK":
        return 10.41
    elif base_currency == "USD" and target_currency == "EUR":
        return 0.92
    else:
        raise ValueError(f"Unknown currency pair '{base_currency}'/'{target_currency}'")

    # Construct the API endpoint with the specified parameters
    api_endpoint = "http://api.exchangerate.host/convert"
    params = {
        "date": date,
        "from": base_currency,
        "to": target_currency,
        "amount": 1,
        "access_key": "819ce799652e09c5a9dea68179d0dbe6",
    }

    # Make the request
    response = requests.get(api_endpoint, params=params)

    # Check if the request was successful
    if response.status_code == 200:
        data = response.json()
        # Extract the exchange rate
        return data["result"]
    else:
        return None
