# 💱 currens

**currens** is a modular Python package for managing exchange rate operations — including fetching, storing, and converting currency values using external APIs like the European Central Bank and Riksbank.

---

## 🚀 Features

- Fetch exchange rates from multiple sources
- Store rates in a local SQL database using SQLAlchemy
- Convert between currencies
- Designed to be extended and modular
- `.env` support for configuration

---

## 📦 Installation

1. Create and activate a virtual environment:

```bash
uv venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
```

2. Install dependencies:

```bash
uv pip install -r requirements.txt
```

Or if you're managing dependencies via `pyproject.toml`:

```bash
uv pip install
```

---

## ⚙️ Configuration

Create a `.env` file in the project root:

```env
DATABASE_URL=sqlite:///db/exchange_rates.db
```

---

## 🧱 Initialize the Database

Before using the application, initialize the database schema. This will also insert the base currencies (EUR, USD, SEK) with their ISO codes:

You can do this in three ways:

```bash
python -m currens --init
```

Or run this standalone script at the project root:

```bash
python init.py
```

Or run the `__main__.py` file directly:

```bash
python currens/__main__.py --init
```

To drop and recreate all tables (useful during development):

```bash
python -m currens --recreate
```

---

## ▶️ Run the Application

Run the main logic (example: store Riksbank exchange rates):

```bash
python -m currens
```

---

## 📂 Project Structure

```
currens/
├── __main__.py
├── collector/
│   ├── __init__.py
│   └── core.py
├── db/
│   ├── models.py
│   ├── session.py
├── apis/
│   └── rate_sources.py
```

---

## 🥪 Coming Soon

- Unit tests
- CLI with `typer`
- More exchange rate providers
- Currency conversion API

---

## 📝 License

MIT License.

---

## 📤 Publishing to GitHub

**Push your code:**

```bash
git remote add origin https://github.com/your-username/currens.git
git branch -M main
git push -u origin main
```

Now your project is live on GitHub!

