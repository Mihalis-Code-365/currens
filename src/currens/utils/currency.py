from sqlalchemy import select
from db.models import Currency
from db.session import get_db_session
from sqlalchemy.dialects import sqlite


def get_currency_iso_code_by_id(currency_id: int) -> str:
    """
    Fetches the ISO code for a given currency ID.
    :param currency_id: Integer ID of the currency
    :return: ISO 4217 currency code (e.g. 'USD', 'EUR')
    """
    with get_db_session() as session:
        try:
            stmt = select(Currency.iso_code).where(Currency.id == currency_id)
            # Print the SQL query with the final values
            # print(
            #     stmt.compile(
            #         dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
            #     )
            # )
            # print(f"Session bind: {session.bind}")
                  
            # Execute the query and fetch the result
            iso_code = session.execute(stmt).scalar()
            if not iso_code:
                raise ValueError
            return iso_code
        except Exception as exc:
            raise ValueError(
                f"Currency with id '{currency_id}' not found or missing iso_code."
            ) from exc
