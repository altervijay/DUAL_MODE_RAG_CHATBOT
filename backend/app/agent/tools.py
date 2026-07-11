"""
Tool implementations + Anthropic tool-use schema definitions.

- search_documents(query) -> [{id, chunk_text, source_file, section_title}]
  `id` is doc_chunks.id — the model cites by id and router.py resolves those
  ids against what this tool actually returned in the turn (ROADMAP §4.2).
  embeds the query locally (same model as ingest_docs.py — must match),
  cosine search against doc_chunks, top-k=5. The corpus is only ~22 chunks
  across 5 files and some questions legitimately need sections from two
  different files (returns_policy.pdf §4 + warranty_policy.pdf), so k stays
  generous relative to corpus size.
- query_orders(sql) -> {"rows": [...]} | {"error": str}
  validated via sql_guard.validate() against the live introspected schema
  BEFORE executing; executed inside a READ ONLY transaction so the DB
  itself rejects any write that somehow got past the guard.
"""

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.agent import sql_guard
from app.config import EMBEDDING_MODEL_NAME
from app.db import get_conn, get_orders_schema

TOP_K = 5

TOOL_DEFS = [
    {
        "name": "search_documents",
        "description": (
            "Semantic search over Northwind Gadgets' policy documents (HR leave, "
            "pricing/discounts, product FAQ, returns/refunds, warranty). Returns the "
            "most relevant policy sections with their source file and section title. "
            "Use for any question about policies, rules, procedures, or product FAQs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query for the policy documents.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_orders",
        "description": (
            "Run a single read-only SQL SELECT against the `orders` table. This is "
            "the ONLY way to answer questions about order data — never answer from "
            "memory. The exact available columns are listed in the system prompt; "
            "referencing any other column or table is rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "One PostgreSQL SELECT statement over the orders table.",
                }
            },
            "required": ["sql"],
        },
    },
]


@lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def search_documents(query: str) -> list[dict]:
    embedding = _model().encode(query).tolist()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, chunk_text, source_file, section_title
            FROM doc_chunks
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (str(embedding), TOP_K),
        ).fetchall()
    return [
        {"id": chunk_id, "chunk_text": text, "source_file": src, "section_title": title}
        for chunk_id, text, src, title in rows
    ]


def query_orders(sql: str) -> dict:
    schema = get_orders_schema()
    is_valid, error = sql_guard.validate(sql, set(schema))
    if not is_valid:
        return {"error": error}

    with get_conn() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            cur = conn.execute(sql)
            columns = [d.name for d in cur.description]
            rows = cur.fetchall()

    return {"rows": [dict(zip(columns, (str(v) for v in row))) for row in rows]}
