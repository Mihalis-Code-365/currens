import sys

from collector.core import init_db, recreate_db, store_riksbank_rates


def main():
    if "--init" in sys.argv:
        init_db()
        print("Database initialized.")
    elif "--recreate" in sys.argv:
        recreate_db()
        print("Database recreated.")
    else:
        store_riksbank_rates(currency_id=2, start_date="2025-01-01")


if __name__ == "__main__":
    main()
