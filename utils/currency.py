from sqlalchemy import select
from db.models import Currency
from db.session import get_db_session


def get_currency_iso_code_by_id(currency_id: int) -> str:
    """
    Fetches the ISO code for a given currency ID.
    :param currency_id: Integer ID of the currency
    :return: ISO 4217 currency code (e.g. 'USD', 'EUR')
    """
    with next(get_db_session()) as session:
        try:
            stmt = select(Currency.iso_code).where(Currency.id == currency_id)
            iso_code = session.execute(stmt).scalar()
            if not iso_code:
                raise ValueError
            return iso_code
        except Exception as exc:
            raise ValueError(
                f"Currency with id '{currency_id}' not found or missing iso_code."
            ) from exc
