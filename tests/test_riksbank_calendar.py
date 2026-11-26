"""#22: a Riksbank date without a rate is checked against the Swedish bank-day calendar."""

from __future__ import annotations

import json
from datetime import date
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

import pytest

from currens import RateNotPublishedError, RateProviderUnavailableError, ensure_rates, get_rate
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


@pytest.mark.parametrize(
    "day",
    [TODAY, date(2026, 11, 27), date(2026, 11, 18), OLD_BANK_DAY, SATURDAY],
    ids=["today", "tomorrow", "eight-days-ago", "2022-gap", "weekend"],
)
def test_dates_outside_the_window_stay_not_published_without_the_calendar(
    monkeypatch, tmp_path, day
) -> None:
    # The calendar is down: it must not matter, since its answer could not change the result.
    urls = _fake_riksbank(monkeypatch, calendar=_calendar_down)

    with pytest.raises(RateNotPublishedError):
        get_rate(day, "USD", "SEK", db_path=tmp_path / "c.db")
    assert not [url for url in urls if "/CalendarDays/" in url]


def test_holiday_inside_the_window_is_not_published(monkeypatch, tmp_path) -> None:
    def calendar_with_holiday(url: str) -> bytes:
        return json.dumps([{"calendarDate": "2026-11-25", "swedishBankday": False}]).encode()

    _fake_riksbank(monkeypatch, calendar=calendar_with_holiday)

    with pytest.raises(RateNotPublishedError):
        get_rate(YESTERDAY, "USD", "SEK", db_path=tmp_path / "c.db")


def test_range_with_old_holidays_and_one_recent_gap_is_unavailable(monkeypatch, tmp_path) -> None:
    def only_yesterday_is_a_bank_day(url: str) -> bytes:
        return json.dumps(
            [
                {"calendarDate": day["calendarDate"], "swedishBankday": day["calendarDate"] == "2026-11-25"}
                for day in json.loads(_calendar(url))
            ]
        ).encode()

    urls = _fake_riksbank(monkeypatch, calendar=only_yesterday_is_a_bank_day)

    with pytest.raises(RateProviderUnavailableError, match="2026-11-25"):
        ensure_rates([("USD", "SEK")], date(2026, 11, 2), YESTERDAY, db_path=tmp_path / "c.db")
    # Only the window is asked about, not the whole range.
    calendar_urls = [url for url in urls if "/CalendarDays/" in url]
    assert calendar_urls == ["https://api.riksbank.se/swea/v1/CalendarDays/2026-11-19/2026-11-25"]


@pytest.mark.parametrize(
    "calendar",
    [
        _calendar_down,
        lambda url: b'[{"date": "2026-11-25"}]',
        lambda url: b'[{"calendarDate": "2026-11-25", "swedishBankday": "no"}]',
        lambda url: json.dumps([{"calendarDate": "2026-11-24", "swedishBankday": False}]).encode(),
    ],
    ids=["calendar-down", "wrong-shape", "non-boolean", "date-missing-from-partial-answer"],
)
def test_calendar_failure_is_unavailable_never_a_guess(monkeypatch, tmp_path, calendar) -> None:
    _fake_riksbank(monkeypatch, calendar=calendar)

    with pytest.raises(RateProviderUnavailableError):
        get_rate(YESTERDAY, "USD", "SEK", db_path=tmp_path / "c.db")


def test_ecb_pairs_do_not_consult_the_riksbank_calendar(monkeypatch, tmp_path) -> None:
    urls = _fake_riksbank(monkeypatch)

    with pytest.raises(RateNotPublishedError):
        get_rate(YESTERDAY, "EUR", "SEK", db_path=tmp_path / "c.db")
    assert not [url for url in urls if "/CalendarDays/" in url]
