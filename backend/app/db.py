"""
Postgres connection (psycopg) + pgvector setup.

- get_pool() / get_conn(): lazy singleton connection pool
- init_db(): CREATE EXTENSION IF NOT EXISTS vector (idempotent, run at startup
  and by the ingestion scripts)
- get_orders_schema(): introspect the live `orders` schema for injection into
  the agent's system prompt and the SQL guard — never hand-type this schema
  elsewhere in the codebase
"""

import atexit

from psycopg_pool import ConnectionPool

from app.config import DATABASE_URL

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=True)
        atexit.register(_pool.close)
    return _pool


def get_conn():
    """Context manager yielding a pooled connection: `with get_conn() as conn:`"""
    return get_pool().connection()


def init_db() -> None:
    with get_conn() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")


def get_orders_schema() -> dict[str, str]:
    """Live `orders` schema as {column_name: data_type}, from information_schema."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'orders'
            ORDER BY ordinal_position
            """
        ).fetchall()
    return {name: dtype for name, dtype in rows}
