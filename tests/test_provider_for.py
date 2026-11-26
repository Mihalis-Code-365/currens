"""#33: `provider_for` names the provider currens asks for a pair, with no network or cache."""

from __future__ import annotations

import pytest

import currens
from currens import UnsupportedCurrencyError, provider_for
from currens import service
from currens.apis import rate_sources


@pytest.fixture
def no_io(monkeypatch, tmp_path):
    """Fail the test on any network call or any touch of the cache."""

    def refuse(*args, **kwargs):
        raise AssertionError("provider_for must not touch the network or the cache")

    monkeypatch.setattr(rate_sources, "urlopen", refuse)
    monkeypatch.setattr(service, "_connect", refuse)
    monkeypatch.setattr(service, "_ensure_schema", refuse)
    # A path that does not exist: a cache read or schema creation would have to create it.
    missing_db = tmp_path / "missing" / "c.db"
    monkeypatch.setattr(service, "_CURRENT_DB_PATH", missing_db)
    yield
    assert not missing_db.parent.exists()


@pytest.mark.parametrize(
    ("base", "target"),
    [("EUR", "USD"), ("USD", "EUR"), ("EUR", "AUD"), ("NOK", "EUR")],
)
def test_a_pair_with_eur_is_the_ecb(no_io, base, target) -> None:
    assert provider_for(base, target) == "ECB"


@pytest.mark.parametrize(
    ("base", "target"),
    [("SEK", "USD"), ("USD", "SEK"), ("GBP", "SEK"), ("SEK", "JPY")],
)
def test_a_pair_with_sek_and_not_eur_is_the_riksbank(no_io, base, target) -> None:
    assert provider_for(base, target) == "Riksbank"


@pytest.mark.parametrize(("base", "target"), [("EUR", "SEK"), ("SEK", "EUR")])
def test_eur_sek_is_the_ecb_as_the_fetch_dispatch_routes_it(
    no_io, base, target
) -> None:
    assert provider_for(base, target) == "ECB"


@pytest.mark.parametrize(("base", "target"), [("GBP", "NOK"), ("USD", "GBP")])
def test_a_cross_pair_is_unsupported(no_io, base, target) -> None:
    with pytest.raises(UnsupportedCurrencyError, match=f"{base}->{target}"):
        provider_for(base, target)


@pytest.mark.parametrize(
    ("base", "target"), [("XYZ", "SEK"), ("EUR", "XYZ"), ("XYZ", "XYZ")]
)
def test_an_unknown_code_is_unsupported(no_io, base, target) -> None:
    with pytest.raises(UnsupportedCurrencyError, match="Unsupported currency 'XYZ'"):
        provider_for(base, target)


@pytest.mark.parametrize(
    ("base", "target"), [("EUR", "EUR"), ("SEK", "SEK"), ("USD", "USD"), ("GBP", "GBP")]
)
def test_a_same_currency_pair_has_no_provider(no_io, base, target) -> None:
    # No provider serves it (`get_rate` answers 1 itself), and currens does support it, so it
    # is a plain ValueError, not an UnsupportedCurrencyError.
    with pytest.raises(ValueError, match="same currency") as excinfo:
        provider_for(base, target)
    assert not isinstance(excinfo.value, UnsupportedCurrencyError)


def test_codes_are_case_insensitive_like_the_other_public_functions(no_io) -> None:
    assert provider_for("eur", "usd") == "ECB"
    assert provider_for("Sek", "gbp") == "Riksbank"
    with pytest.raises(ValueError, match="EUR->EUR is the same currency"):
        provider_for("eur", "EUR")


def test_it_is_exported() -> None:
    assert "provider_for" in currens.__all__
    assert currens.provider_for is service.provider_for
    assert "RateProvider" in currens.__all__
    assert currens.RateProvider is service.RateProvider
