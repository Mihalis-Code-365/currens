from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, DivisionByZero, InvalidOperation
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from currens import RateNotPublishedError, RateProviderUnavailableError
from currens.apis import rate_sources
from currens.service import get_rate

# Good Friday: neither provider publishes a rate. The Wednesday before is the control.
HOLIDAY = date(2025, 4, 18)
CONTROL = date(2025, 4, 16)

# ECB pairs involve EUR; USD->SEK has no EUR leg and goes to the Riksbank.
ECB_PAIR = ("USD", "EUR")
RIKSBANK_PAIR = ("USD", "SEK")


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        return None


def _calendar_body(url: str) -> bytes:
    """A Riksbank CalendarDays answer: weekdays are bank days, except Easter 2025 (#22)."""
    start, end = (date.fromisoformat(part) for part in url.rstrip("/").split("/")[-2:])
    days = []
    while start <= end:
        is_bank_day = start.weekday() < 5 and start not in (HOLIDAY, date(2025, 4, 21))
        days.append({"calendarDate": start.isoformat(), "swedishBankday": is_bank_day})
        start = start.fromordinal(start.toordinal() + 1)
    return json.dumps(days).encode()


def _respond(monkeypatch, *, status: int = 200, body: bytes = b"", raises: Exception | None = None):
    def fake_urlopen(url, timeout=None):
        if raises is not None:
            raise raises
        # The rate answers are what these tests vary; the calendar answers truthfully.
        if "/CalendarDays/" in url:
            return _FakeResponse(200, _calendar_body(url))
        return _FakeResponse(status, body)

    monkeypatch.setattr(rate_sources, "urlopen", fake_urlopen)


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://provider.test", code, "error", Message(), BytesIO(b""))


def _ecb_body(day: date, value: float) -> bytes:
    return json.dumps(
        {
            "dataSets": [{"series": {"0:0:0:0:0": {"observations": {"0": [value]}}}}],
            "structure": {"dimensions": {"observation": [{"values": [{"id": day.isoformat()}]}]}},
        }
    ).encode()


def _riksbank_body(day: date, value: float) -> bytes:
    return json.dumps([{"date": day.isoformat(), "value": value}]).encode()


@pytest.mark.parametrize(
    ("pair", "status"),
    [(ECB_PAIR, 200), (ECB_PAIR, 204), (RIKSBANK_PAIR, 204), (RIKSBANK_PAIR, 200)],
)
def test_holiday_raises_rate_not_published(monkeypatch, tmp_path, pair, status) -> None:
    _respond(monkeypatch, status=status, body=b"")

    with pytest.raises(RateNotPublishedError):
        get_rate(HOLIDAY, *pair, db_path=tmp_path / "currens.db")


@pytest.mark.parametrize("pair", [ECB_PAIR, RIKSBANK_PAIR])
@pytest.mark.parametrize(
    "failure",
    [URLError("connection refused"), TimeoutError("timed out"), _http_error(500), _http_error(400)],
    ids=["url-error", "timeout", "http-500", "http-400"],
)
def test_provider_failure_raises_unavailable_not_value_error(
    monkeypatch, tmp_path, pair, failure
) -> None:
    _respond(monkeypatch, raises=failure)

    with pytest.raises(RateProviderUnavailableError) as excinfo:
        get_rate(CONTROL, *pair, db_path=tmp_path / "currens.db")

    assert not isinstance(excinfo.value, ValueError)


@pytest.mark.parametrize("pair", [ECB_PAIR, RIKSBANK_PAIR])
def test_unparseable_body_raises_unavailable(monkeypatch, tmp_path, pair) -> None:
    _respond(monkeypatch, body=b"<html>Service Unavailable</html>")

    with pytest.raises(RateProviderUnavailableError):
        get_rate(CONTROL, *pair, db_path=tmp_path / "currens.db")


def test_ecb_unexpected_json_shape_raises_unavailable(monkeypatch, tmp_path) -> None:
    _respond(monkeypatch, body=b'{"dataSets": []}')

    with pytest.raises(RateProviderUnavailableError):
        get_rate(CONTROL, *ECB_PAIR, db_path=tmp_path / "currens.db")


def test_ecb_normal_path_returns_rate(monkeypatch, tmp_path) -> None:
    # ECB quotes USD per EUR; USD->EUR is the inverse.
    _respond(monkeypatch, body=_ecb_body(CONTROL, 1.25))

    assert get_rate(CONTROL, *ECB_PAIR, db_path=tmp_path / "currens.db") == Decimal("0.8")


@pytest.mark.parametrize(
    ("pair", "expected"),
    [(("USD", "SEK"), Decimal("10")), (("SEK", "USD"), Decimal("0.1"))],
)
def test_riksbank_normal_path_returns_rate(monkeypatch, tmp_path, pair, expected) -> None:
    # Riksbank SEKUSDPMI quotes SEK per 1 USD, so USD->SEK is the value as published.
    _respond(monkeypatch, body=_riksbank_body(CONTROL, 10.0))

    assert get_rate(CONTROL, *pair, db_path=tmp_path / "currens.db") == expected


@pytest.mark.parametrize(
    ("pair", "payload", "cause"),
    [
        (RIKSBANK_PAIR, [{"date": "2025-04-16T00:00:00", "value": 10.0}], ValueError),
        (RIKSBANK_PAIR, [{"value": 10.0}], ValueError),
        (RIKSBANK_PAIR, [{"date": "2025-04-16", "value": "n/a"}], ValueError),
        (RIKSBANK_PAIR, [{"date": "2025-04-16", "value": [10.0]}], TypeError),
        (RIKSBANK_PAIR, {"data": [{"date": "2025-04-16", "value": "n/a"}]}, InvalidOperation),
        (("SEK", "USD"), [{"date": "2025-04-16", "value": 0}], DivisionByZero),
        (RIKSBANK_PAIR, ["not-a-row"], AttributeError),
    ],
    ids=[
        "bad-date",
        "missing-date",
        "non-numeric-value",
        "wrong-value-type",
        "non-numeric-value-in-data-envelope",
        "zero-on-reversed-pair",
        "row-not-a-dict",
    ],
)
def test_malformed_row_raises_unavailable(monkeypatch, tmp_path, pair, payload, cause) -> None:
    _respond(monkeypatch, body=json.dumps(payload).encode())

    with pytest.raises(RateProviderUnavailableError, match="Riksbank") as excinfo:
        get_rate(CONTROL, *pair, db_path=tmp_path / "currens.db")

    bad_row = payload["data"][0] if isinstance(payload, dict) else payload[0]
    assert repr(bad_row) in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, cause)


@pytest.mark.parametrize(
    ("observations", "time_periods"),
    [
        ({"0": []}, [{"id": "2025-04-16"}]),
        ({"0": {}}, [{"id": "2025-04-16"}]),
        ({"0": None}, [{"id": "2025-04-16"}]),
        ([[1.25]], [{"id": "2025-04-16"}]),
        ({"0": [1.25]}, [{}]),
        ({"0": [1.25]}, ["2025-04-16"]),
    ],
    ids=[
        "empty-observation",
        "observation-without-value",
        "null-observation",
        "observations-not-a-mapping",
        "period-without-id",
        "period-not-a-mapping",
    ],
)
def test_ecb_malformed_entry_raises_unavailable(
    monkeypatch, tmp_path, observations, time_periods
) -> None:
    body = {
        "dataSets": [{"series": {"0:0:0:0:0": {"observations": observations}}}],
        "structure": {"dimensions": {"observation": [{"values": time_periods}]}},
    }
    _respond(monkeypatch, body=json.dumps(body).encode())

    with pytest.raises(RateProviderUnavailableError, match="ECB"):
        get_rate(CONTROL, *ECB_PAIR, db_path=tmp_path / "currens.db")


def _ecb_series(observations, periods) -> bytes:
    return json.dumps(
        {
            "dataSets": [{"series": {"0:0:0:0:0": {"observations": observations}}}],
            "structure": {"dimensions": {"observation": [{"values": periods}]}},
        }
    ).encode()


TWO_PERIODS = [{"id": "2025-04-15"}, {"id": "2025-04-16"}]


@pytest.mark.parametrize(
    "observations",
    [{"0": [1.1], "1": [1.25]}, {"1": [1.25], "0": [1.1]}],
    ids=["keys-in-order", "keys-out-of-order"],
)
def test_ecb_observation_is_matched_to_its_period_by_key(
    monkeypatch, tmp_path, observations
) -> None:
    # The key is the period's index, so key order must not matter. USD->EUR is 1 / 1.25.
    _respond(monkeypatch, body=_ecb_series(observations, TWO_PERIODS))

    assert get_rate(CONTROL, *ECB_PAIR, db_path=tmp_path / "currens.db") == Decimal("0.8")


@pytest.mark.parametrize(
    ("observations", "periods"),
    [
        ({"0": "1.25"}, [{"id": "2025-04-16"}]),
        ({"5": [1.25]}, [{"id": "2025-04-16"}]),
        ({"x": [1.25]}, [{"id": "2025-04-16"}]),
        ({"1": [1.25]}, TWO_PERIODS),
        ({"0": [1.1], "1": [1.25]}, [{"id": "2025-04-16"}]),
    ],
    ids=[
        "observation-is-a-string",
        "key-out-of-range",
        "key-not-an-index",
        "period-without-observation",
        "observation-without-period",
    ],
)
def test_ecb_series_that_does_not_line_up_raises_unavailable(
    monkeypatch, tmp_path, observations, periods
) -> None:
    _respond(monkeypatch, body=_ecb_series(observations, periods))

    with pytest.raises(RateProviderUnavailableError, match="ECB"):
        get_rate(CONTROL, *ECB_PAIR, db_path=tmp_path / "currens.db")


@pytest.mark.parametrize(
    ("pair", "body"),
    [
        (RIKSBANK_PAIR, b'[{"date": "2025-04-16", "value": 0}]'),
        (RIKSBANK_PAIR, b'[{"date": "2025-04-16", "value": -10}]'),
        (RIKSBANK_PAIR, b'[{"date": "2025-04-16", "value": Infinity}]'),
        (RIKSBANK_PAIR, b'[{"date": "2025-04-16", "value": 1e400}]'),
        (("SEK", "USD"), b'[{"date": "2025-04-16", "value": -10}]'),
        (("EUR", "USD"), _ecb_body(CONTROL, 0)),
    ],
    ids=["zero", "negative", "infinity", "overflow-to-infinity", "negative-reversed", "ecb-zero"],
)
def test_rate_that_is_not_finite_and_positive_raises_unavailable(
    monkeypatch, tmp_path, pair, body
) -> None:
    _respond(monkeypatch, body=body)

    with pytest.raises(RateProviderUnavailableError):
        get_rate(CONTROL, *pair, db_path=tmp_path / "currens.db")


@pytest.mark.parametrize(
    "body",
    [b"5", b"true", b"null", b'""', b"{}", b'{"data": null}', b'{"data": 5}', b'{"data": {}}'],
    ids=["number", "boolean", "null", "string", "empty-object", "data-null", "data-number", "data-object"],
)
def test_riksbank_body_that_is_not_a_list_raises_unavailable(
    monkeypatch, tmp_path, body
) -> None:
    _respond(monkeypatch, body=body)

    with pytest.raises(RateProviderUnavailableError):
        get_rate(CONTROL, *RIKSBANK_PAIR, db_path=tmp_path / "currens.db")


def test_ecb_null_body_raises_unavailable(monkeypatch, tmp_path) -> None:
    # A day without a fixing is an empty body, never a JSON null.
    _respond(monkeypatch, body=b"null")

    with pytest.raises(RateProviderUnavailableError):
        get_rate(CONTROL, *ECB_PAIR, db_path=tmp_path / "currens.db")


@pytest.mark.parametrize(
    ("pair", "body"),
    [
        (RIKSBANK_PAIR, b"[]"),
        (RIKSBANK_PAIR, b'[{"date": "2025-04-16", "value": null}]'),
        (RIKSBANK_PAIR, b'[{"date": "2025-04-16", "value": NaN}]'),
        (ECB_PAIR, _ecb_series({"0": [None]}, [{"id": "2025-04-16"}])),
        (ECB_PAIR, b'{"dataSets": [{"series": {"0:0:0:0:0": {"observations": {"0": [NaN]}}}}],'
                   b' "structure": {"dimensions": {"observation": [{"values": [{"id": "2025-04-16"}]}]}}}'),
        (ECB_PAIR, _ecb_series({}, [])),
    ],
    ids=["riksbank-empty-list", "riksbank-null", "riksbank-nan", "ecb-null", "ecb-nan", "ecb-empty-series"],
)
def test_provider_saying_no_rate_raises_not_published(monkeypatch, tmp_path, pair, body) -> None:
    # A null or NaN value is the provider's "no rate", so the caller may walk back a day.
    _respond(monkeypatch, body=body)

    with pytest.raises(RateNotPublishedError):
        get_rate(CONTROL, *pair, db_path=tmp_path / "currens.db")


@pytest.mark.parametrize(
    ("value", "cause"),
    [("n/a", ValueError), ([1.25], TypeError)],
    ids=["non-numeric-value", "wrong-value-type"],
)
def test_ecb_malformed_value_raises_unavailable(monkeypatch, tmp_path, value, cause) -> None:
    _respond(monkeypatch, body=_ecb_series({"0": [value]}, [{"id": "2025-04-16"}]))

    with pytest.raises(RateProviderUnavailableError, match="EuropeanCentralBank") as excinfo:
        get_rate(CONTROL, *ECB_PAIR, db_path=tmp_path / "currens.db")

    assert isinstance(excinfo.value.__cause__, cause)
