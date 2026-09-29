from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
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


def _respond(monkeypatch, *, status: int = 200, body: bytes = b"", raises: Exception | None = None):
    def fake_urlopen(url, timeout=None):
        if raises is not None:
            raise raises
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
