from db.models import Base, Currency, ExchangeRate
from db.session import engine, SessionLocal, get_db_session
from apis.rate_sources import (
    get_exchange_rates_from_european_central_bank,
    get_exchange_rates_from_riksbank,
)
from datetime import datetime

from utils.currency import get_currency_iso_code_by_id


def init_db():
    print(_("Initializing the database..."))
    Base.metadata.create_all(bind=engine)
    print(_("Database tables created."))

    # Pre-populate base currencies
    session = SessionLocal()
    try:
        if not session.query(Currency).count():
            print("Pre-populating base currencies...")
            currencies = [
                Currency(id=1, name="Euro", iso_code="EUR"),
                Currency(id=2, name="US Dollar", iso_code="USD"),
                Currency(id=3, name="Swedish Krona", iso_code="SEK"),
            ]
            session.add_all(currencies)
            session.commit()
            print("Inserted base currencies: EUR, USD, SEK")
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
    Base.metadata.drop_all(bind=engine)
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
            start_date=start_date,
            end_date=end_date or _("today"),
        )
    )

    data = get_exchange_rates_from_riksbank(currency_id, start_date, end_date)
    if isinstance(data, str):
        print(f"Error fetching data: {data}")
        return

    print(f"Fetched {len(data)} records. Storing them in the database...")
    with get_db_session() as session:
        for record in data:
            rate = ExchangeRate(
                source="Riksbank",
                exchange_rate_date=datetime.strptime(record["date"], "%Y-%m-%d").date(),
                base_currency_id=1,  # set appropriately
                target_currency_id=currency_id,
                value=float(record["value"]),
            )
            session.merge(rate)
        session.commit()
    print(f"Successfully stored exchange rates for currency {currency_iso_code}.")


def store_european_central_bank_rates(
    base_currency_id: int, rate_currency_id: int, start_date: str, end_date: str = None
):
    base_currency_iso_code = get_currency_iso_code_by_id(base_currency_id)
    rate_currency_iso_code = get_currency_iso_code_by_id(rate_currency_id)
    message = "Fetching exchange rates from European Central Bank for base currency {base_currency_iso_code} and rate currency {rate_currency_iso_code} from {start_date} to {end_date}..."

    print(message)

    data = get_exchange_rates_from_european_central_bank(
        base_currency_id, rate_currency_id, start_date, end_date
    )
    if isinstance(data, str):
        print(f"Error fetching data: {data}")
        return

    print(f"Fetched {len(data)} records. Storing them in the database...")
    with get_db_session() as session:
        for record in data:
            rate = ExchangeRate(
                source="EuropeanCentralBank",
                exchange_rate_date=datetime.strptime(record["date"], "%Y-%m-%d").date(),
                base_currency_id=base_currency_id,
                target_currency_id=rate_currency_id,
                value=float(record["value"]),
            )
            session.merge(rate)
        session.commit()
    print(
        f"Successfully stored exchange rates for base currency {base_currency_iso_code} and rate currency {rate_currency_iso_code}."
    )
