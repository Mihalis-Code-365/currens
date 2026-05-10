import sys

from currens.collector.core import (
    init_db,
    recreate_db,
    store_european_central_bank_rates,
    store_riksbank_rates,
)
from currens.service import import_mihalis_rates
from currens.utils.multilanguage_support import setup_translation


def main():
    setup_translation("el")
    if "--init" in sys.argv:
        init_db()
        print("Database initialized.")
    elif "--recreate" in sys.argv:
        recreate_db()
        print("Database recreated.")
    elif "--import-mihalis-rates" in sys.argv:
        try:
            source_db_path = sys.argv[sys.argv.index("--import-mihalis-rates") + 1]
        except IndexError as exc:
            raise SystemExit("--import-mihalis-rates requires a source database path.") from exc
        imported = import_mihalis_rates(source_db_path)
        print(f"Imported {imported} exchange-rate rows from Mihalis.")
    else:
        # store_riksbank_rates(currency_id=2, start_date="2025-01-01")
        store_european_central_bank_rates(
            base_currency_id=1,
            rate_currency_id=2,
            start_date="2025-04-01",
        )


if __name__ == "__main__":
    main()
