"""#22: a Riksbank date without a rate is checked against the Swedish bank-day calendar."""

from __future__ import annotations

import json
from datetime import date
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

import pytest

from currens import RateNotPublishedError, RateProviderUnavailableError, ensure_rates_for_dates, get_rate
from currens import service
from currens.apis import rate_sources

TODAY = date(2026, 11, 26)  # a Thursday
YESTERDAY = date(2026, 11, 25)
WEEK_AGO = date(2026, 11, 19)  # 7 days back: still inside the window
OLD_BANK_DAY = date(2022, 2, 2)  # the real SEKEURPMI gap
SATURDAY = date(2026, 11, 21)


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        return None


def _calendar(url: str) -> bytes:
    start, end = (date.fromisoformat(part) for part in url.rstrip("/").split("/")[-2:])
    days = []
    while start <= end:
        days.append({"calendarDate": start.isoformat(), "swedishBankday": start.weekday() < 5})
        start = date.fromordinal(start.toordinal() + 1)
    return json.dumps(days).encode()


def _fake_riksbank(monkeypatch, *, calendar=_calendar) -> list[str]:
    """Every rate request gets "no content"; `calendar(url)` returns the calendar's bytes."""
    urls: list[str] = []

    def fake_urlopen(url, timeout=None):
        urls.append(url)
        if "/CalendarDays/" in url:
            return _Response(200, calendar(url))
        return _Response(204, b"")

    monkeypatch.setattr(rate_sources, "urlopen", fake_urlopen)
    monkeypatch.setattr(service, "_stockholm_today", lambda: TODAY)
    return urls


def _calendar_down(url: str) -> bytes:
    raise HTTPError(url, 503, "error", Message(), BytesIO(b""))


@pytest.mark.parametrize("day", [YESTERDAY, WEEK_AGO], ids=["yesterday", "seven-days-ago"])
def test_recent_bank_day_without_rate_is_unavailable(monkeypatch, tmp_path, day) -> None:
    _fake_riksbank(monkeypatch)

    with pytest.raises(RateProviderUnavailableError, match="Swedish bank day"):
        get_rate(day, "USD", "SEK", db_path=tmp_path / "c.db")


def test_today_without_rate_is_not_published_yet(monkeypatch, tmp_path) -> None:
    # The Riksbank publishes in the afternoon; a caller valuing "today" walks back a day.
    _fake_riksbank(monkeypatch)

    with pytest.raises(RateNotPublishedError):
        get_rate(TODAY, "USD", "SEK", db_path=tmp_path / "c.db")


def test_old_bank_day_gap_is_final_and_logged(monkeypatch, tmp_path, caplog) -> None:
    _fake_riksbank(monkeypatch)

    with pytest.raises(RateNotPublishedError):
        get_rate(OLD_BANK_DAY, "GBP", "SEK", db_path=tmp_path / "c.db")
    assert "2022-02-02" in caplog.text
    assert "treating the gap as final" in caplog.text


def test_non_bank_day_is_not_published(monkeypatch, tmp_path) -> None:
    _fake_riksbank(monkeypatch)

    with pytest.raises(RateNotPublishedError):
        get_rate(SATURDAY, "SEK", "USD", db_path=tmp_path / "c.db")


def test_one_calendar_call_covers_all_missing_dates(monkeypatch, tmp_path) -> None:
    urls = _fake_riksbank(monkeypatch)
    old_dates = [date(2022, 2, 1), date(2022, 2, 2), date(2022, 2, 3)]

    with pytest.raises(RateNotPublishedError):
        ensure_rates_for_dates([("USD", "SEK")], old_dates, db_path=tmp_path / "c.db")

    calendar_urls = [url for url in urls if "/CalendarDays/" in url]
    assert calendar_urls == ["https://api.riksbank.se/swea/v1/CalendarDays/2022-02-01/2022-02-03"]


@pytest.mark.parametrize(
    "calendar",
    [
        _calendar_down,
        lambda url: b'[{"date": "2026-11-21"}]',
        lambda url: b'[{"calendarDate": "2026-11-21", "swedishBankday": "no"}]',
        lambda url: b"[]",
    ],
    ids=["calendar-down", "wrong-shape", "non-boolean", "date-missing"],
)
def test_calendar_failure_is_unavailable_never_a_guess(monkeypatch, tmp_path, calendar) -> None:
    _fake_riksbank(monkeypatch, calendar=calendar)

    with pytest.raises(RateProviderUnavailableError):
        get_rate(SATURDAY, "USD", "SEK", db_path=tmp_path / "c.db")


def test_ecb_pairs_do_not_consult_the_riksbank_calendar(monkeypatch, tmp_path) -> None:
    urls = _fake_riksbank(monkeypatch)

    with pytest.raises(RateNotPublishedError):
        get_rate(YESTERDAY, "EUR", "SEK", db_path=tmp_path / "c.db")
    assert not [url for url in urls if "/CalendarDays/" in url]
