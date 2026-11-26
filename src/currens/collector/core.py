from currens.apis.rate_sources import (
    get_exchange_rates_from_european_central_bank,
    get_exchange_rates_from_riksbank,
)
from currens.db import session as db_session
from currens.db.models import Base, Currency, ExchangeRate
from currens.db.session import get_db_session
from currens.errors import RateProviderUnavailableError
# The service's row checks, so the collector cannot store what get_rate would refuse (#20).
from currens.service import SEK_CURRENCY_ID, ExchangeRateRecord, _normalize_provider_rows
from currens.utils.currency import CURRENCIES, get_currency_iso_code_by_id


def init_db():
    print(_("Initializing the database..."))
    Base.metadata.create_all(bind=db_session.engine)
    print(_("Database tables created."))

    # Pre-populate base currencies
    session = db_session.SessionLocal()
    try:
        # Adds whatever the table lacks, so a database seeded with fewer currencies catches up.
        existing = session.query(Currency.id, Currency.iso_code).all()
        existing_ids = {currency_id for currency_id, _code in existing}
        existing_codes = {code for _id, code in existing}
        missing = [
            row for row in CURRENCIES if row[0] not in existing_ids and row[1] not in existing_codes
        ]
        if missing:
            print("Pre-populating base currencies...")
            session.add_all(
                Currency(id=currency_id, name=name, iso_code=iso_code)
                for currency_id, iso_code, name in missing
            )
            session.commit()
            print("Inserted base currencies: " + ", ".join(row[1] for row in missing))
        else:
            print("Base currencies already exist. Skipping pre-population.")
    finally:
        session.close()
        print("Database initialization complete.")


def recreate_db():
    """
    Drops and recreates all tables. Use with caution in production.
    """
    print("Dropping all tables...")
    Base.metadata.drop_all(bind=db_session.engine)
    print("All tables dropped.")
    init_db()


def store_riksbank_rates(currency_id: int, start_date: str, end_date: str = None):
    currency_iso_code = get_currency_iso_code_by_id(currency_id)
    message = _(
        "Fetching exchange rates from Riksbank for currency {currency_iso_code} from {start_date} to {end_date}..."
    )
    print(
        message.format(
            currency_id=currency_id,
            currency_iso_code=currency_iso_code,
            start_date=start_date,
            end_date=end_date or _("today"),
        )
    )

    try:
        data = get_exchange_rates_from_riksbank(currency_id, start_date, end_date)
        # Riksbank series quote SEK per 1 unit of the foreign currency.
        records = _normalize_provider_rows(
            rows=data,
            source="Riksbank",
            base_currency_id=currency_id,
            rate_currency_id=SEK_CURRENCY_ID,
            invert_rate=False,
        )
    except (RateProviderUnavailableError, ValueError) as e:
        print(f"Error fetching data: {e}")
        return

    _store_records(records)
    print(f"Successfully stored exchange rates for currency {currency_iso_code}.")


def store_european_central_bank_rates(
    base_currency_id: int, rate_currency_id: int, start_date: str, end_date: str = None
):
    base_currency_iso_code = get_currency_iso_code_by_id(base_currency_id)
    rate_currency_iso_code = get_currency_iso_code_by_id(rate_currency_id)
    message = _(
        "Fetching exchange rates from European Central Bank for base currency {base_currency_iso_code} and rate currency {rate_currency_iso_code} from {start_date} to {end_date}..."
    )
    print(
        message.format(
            base_currency_iso_code=base_currency_iso_code,
            rate_currency_iso_code=rate_currency_iso_code,
            start_date=start_date,
            end_date=end_date or _("today"),
        )
    )

    try:
        data = get_exchange_rates_from_european_central_bank(
            base_currency_id, rate_currency_id, start_date, end_date
        )
        records = _normalize_provider_rows(
            rows=data,
            source="EuropeanCentralBank",
            base_currency_id=base_currency_id,
            rate_currency_id=rate_currency_id,
            invert_rate=False,
        )
    except (RateProviderUnavailableError, ValueError) as e:
        print(f"Error fetching data: {e}")
        return

    _store_records(records)
    print(
        f"Successfully stored exchange rates for base currency {base_currency_iso_code} and rate currency {rate_currency_iso_code}."
    )


def _store_records(records: list[ExchangeRateRecord]) -> None:
    print(f"Fetched {len(records)} records. Storing them in the database...")
    with get_db_session() as session:
        for record in records:
            session.merge(
                ExchangeRate(
                    source=record.source,
                    exchange_rate_date=record.exchange_rate_date,
                    base_currency_id=record.base_currency_id,
                    rate_currency_id=record.rate_currency_id,
                    rate=float(record.rate),
                )
            )
        session.commit()
