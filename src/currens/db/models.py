"""
This module contains the models for the database.
"""

from sqlalchemy import (
    ForeignKey,
    Integer,
    Float,
    String,
    Date,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import mapped_column, Mapped


class Base(DeclarativeBase):
    """
    Base class for all models.
    """

    pass


class Currency(Base):
    __tablename__ = "currencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    iso_code: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)


class ExchangeRate(Base):
    """
    Represents an exchange rate in the database.
    """

    __tablename__ = "exchange_rates"

    # id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)
    exchange_rate_date: Mapped[Date] = mapped_column(Date, primary_key=True)
    base_currency_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("currencies.id"), primary_key=True
    )
    target_currency_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("currencies.id"), primary_key=True
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
