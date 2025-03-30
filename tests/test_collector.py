from exchange_rates_collector.collector import init_db, store_riksbank_rates

def test_store_riksbank_rates():
    init_db()
    store_riksbank_rates(currency_id=2, start_date="2024-01-01")
