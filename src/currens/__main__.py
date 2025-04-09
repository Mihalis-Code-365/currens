import sys

from collector.core import (
    init_db,
    recreate_db,
    store_european_central_bank_rates,
    store_riksbank_rates,
)
from utils.multilanguage_support import setup_translation


def main():
    setup_translation("el")
    if "--init" in sys.argv:
        init_db()
        print("Database initialized.")
    elif "--recreate" in sys.argv:
        recreate_db()
        print("Database recreated.")
    else:
        # store_riksbank_rates(currency_id=2, start_date="2025-01-01")
        store_european_central_bank_rates(
            base_currency_id=1,
            rate_currency_id=2,
            start_date="2025-04-01",
        )


if __name__ == "__main__":
    main()
