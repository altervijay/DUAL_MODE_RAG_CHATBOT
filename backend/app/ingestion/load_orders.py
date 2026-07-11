"""
One-shot script: create `orders` table from the provided CSV and bulk load.
Run manually (`python -m app.ingestion.load_orders`), not on every backend
boot.

Loads backend/data/orders.csv as-is. No delivery_date or quantity column —
don't add or derive one. Two rows break the ORD-1NNN sequential pattern
(ORD-1207, ORD-1233) and are loaded verbatim rather than corrected.
"""

import pandas as pd

from app.config import DATA_DIR
from app.db import get_conn, init_db

CSV_PATH = DATA_DIR / "orders.csv"


def main() -> None:
    init_db()
    df = pd.read_csv(CSV_PATH)

    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id   text PRIMARY KEY,
                customer   text NOT NULL,
                product    text NOT NULL,
                amount     numeric NOT NULL,
                status     text NOT NULL,
                order_date date NOT NULL
            )
            """
        )
        conn.execute("TRUNCATE orders")  # idempotent re-runs
        with conn.cursor() as cur:
            with cur.copy(
                "COPY orders (order_id, customer, product, amount, status, order_date) FROM STDIN"
            ) as copy:
                for row in df.itertuples(index=False):
                    copy.write_row(tuple(row))

        count = conn.execute("SELECT count(*) FROM orders").fetchone()[0]

    print(f"Loaded {count} rows into orders (expected {len(df)}).")


if __name__ == "__main__":
    main()
