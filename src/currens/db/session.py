from contextlib import contextmanager
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# Load environment variables
load_dotenv()

PACKAGE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PACKAGE_DIR / "db" / "exchange_rates.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"

DATABASE_URL = os.getenv("CURRENS_DATABASE_URL") or os.getenv(
    "DATABASE_URL", DEFAULT_DATABASE_URL
)


def _build_engine(database_url: str):
    return create_engine(database_url, echo=False)


engine = _build_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def configure_database(*, database_url: str | None = None, db_path: str | Path | None = None):
    global DATABASE_URL, engine, SessionLocal

    if database_url and db_path:
        raise ValueError("Use either database_url or db_path, not both.")

    if db_path is not None:
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{path.as_posix()}"

    if database_url is None:
        database_url = DEFAULT_DATABASE_URL

    DATABASE_URL = database_url
    engine = _build_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_database_url() -> str:
    return DATABASE_URL


@contextmanager
def get_db_session():
    """
    Yields a SQLAlchemy session using a context manager.
    Usage:
        with get_db_session() as session:
            session.query(...)
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
