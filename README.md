# Dual-Mode Agentic RAG Chatbot

A support chatbot for Northwind Gadgets, a fictional retailer, built for the EMB
Global AI Engineer technical assessment. It answers questions from two separate
knowledge sources and decides for itself, per question, which one to use:

1. **Documents** — five policy PDFs (HR leave, pricing & discounts, product FAQ,
   returns & refunds, warranty), chunked by section and retrieved with vector
   search.
2. **Structured data** — an `orders` table (200 rows), queried through
   generated SQL rather than embedded for retrieval.

Document questions are answered through RAG with citations. Data questions are
answered through validated text-to-SQL. Mixed questions use both sources in the
same turn. Order data is never answered from the model's memory — it always
goes through a live query.

## Live demo

- Frontend: https://dual-mode-rag-chatbot.vercel.app
- Backend: https://dual-mode-rag-chatbot.onrender.com (`GET /health`)

The backend runs on Render's free tier by choice. It spins down after ~15
minutes of inactivity, so the first request after a gap can take a minute or
two to come back while the instance restarts and reloads the embedding model.
Subsequent requests are fast. This is a deliberate tradeoff, not a bug.

## Architecture

```
Next.js chat UI ──POST /chat (SSE)──> FastAPI ──tool-use loop──> Claude
                                        │
                        ┌───────────────┴────────────────┐
                 search_documents                  query_orders
                 (pgvector cosine                  (sqlglot-validated
                  search over doc_chunks)           SELECT on orders)
                        └───────── Postgres + pgvector ──┘
```

**Routing.** There's no separate classifier — the tool-use loop *is* the
router. Claude decides per turn whether to call `search_documents`,
`query_orders`, both, or answer directly. Every turn is a single streaming
call: the first `content_block_start` event tells the router what kind of
turn it is — a `tool_use` block gets accumulated and executed, a `text` block
gets relayed to the client token by token as it arrives. There's no hidden
decision pass and no wasted generation.

**Model.** `claude-sonnet-5`, via the Anthropic API with native tool use. This
workload — tool routing, retrieval over a small corpus, narrow SQL generation
— doesn't need a frontier-tier model.

**Embeddings.** Local `sentence-transformers` (`all-MiniLM-L6-v2`), used both
at ingest time and at query time. No external API cost, fully deterministic,
and the corpus is small enough (~22 chunks) that retrieval quality isn't the
bottleneck.

**Vector store.** Postgres with the `pgvector` extension, in the same database
as the `orders` table. One database for both keeps the infrastructure simple,
and it also makes "structured data is never embedded" true by construction —
`doc_chunks` and `orders` are separate tables with separate one-time ingestion
scripts.

**Chunking.** One chunk per policy section, split on the documents' own
headings rather than fixed token windows.

**Citations.** `search_documents` tags each chunk with its `doc_chunks.id`.
The model closes a document-based answer with a `CITATIONS: [id, ...]` line,
which the router strips out of the streamed text and resolves against the
ids that were actually retrieved earlier in that turn. The citation shown in
the UI comes from that lookup, not from the model's own text — so a filename
or section title can't be hallucinated, because the model never writes one
directly.

## Guardrails

- **SQL validation** (`app/agent/sql_guard.py`) — every generated query is
  parsed with `sqlglot`: exactly one `SELECT`, only the `orders` table, and
  every column checked against the schema introspected from the live
  database. Queries also run inside a read-only transaction as a second
  layer of protection.
- **No invented columns** — the system prompt is built from the live schema,
  and `orders` has no `delivery_date` or `quantity` column. Questions that
  depend on either (return windows measured from delivery, bulk-discount
  eligibility by unit count) get an honest "that isn't tracked" instead of a
  guess.
- **Fallback** — anything out of scope, or anything without supporting
  evidence from a tool call, gets exactly "I don't have that information."
- **Fixed date** — the assessment date (2026-06-15) is a constant, never
  `datetime.now()`, so relative-time questions resolve consistently.
- **Exact order lookups** — order IDs are matched as exact strings. A couple
  of IDs in the dataset break the usual numbering pattern on purpose, and a
  nonexistent order returns "not found" rather than a fabricated answer.
- **No silent failures** — if the model call fails mid-stream, the error is
  caught and sent to the client as an `error` event instead of leaving the
  chat bubble blank.

## Setup

```bash
cp .env.example .env   # fill in ANTHROPIC_API_KEY
docker compose up --build
# one-time, once the db service is up:
docker compose exec backend python -m app.ingestion.load_orders
docker compose exec backend python -m app.ingestion.ingest_docs
```

Frontend: http://localhost:3000
Backend: http://localhost:8000 (`GET /health`, `POST /chat` — an SSE stream
of `tool`, `token`, `citations`, `error`, and `done` events)

## Tests

- `backend/tests/test_brief.py` — end-to-end tests against a running backend:
  document, data, and mixed questions, plus the dataset's specific edge cases
  (a nonexistent order, an order ID that breaks the usual pattern, and a
  question that legitimately needs citations from two files). Run with
  `python -m tests.test_brief <base_url>`.
- `backend/tests/test_stream_filter.py` — mocked unit tests for the router's
  streaming logic: citation-marker filtering across delta boundaries,
  citation-by-id resolution, and a couple of regressions around thinking
  blocks and mixed turns. No API key or network access needed. Run with
  `python -m tests.test_stream_filter`.

## Known limitations

- Local embeddings trade some retrieval recall for zero external dependency.
  Fine at this corpus size; wouldn't scale as-is.
- `orders` has no `delivery_date` or `quantity`, so anything depending on
  those is answered approximately or declared out of scope, by design.
- No conversation memory beyond a single turn's tool-use loop — each question
  is handled independently.
- The SQL guard is intentionally narrow: one table, no joins, no CTEs. It's
  not meant to be a general text-to-SQL layer.
- `claude-sonnet-5` doesn't accept a `temperature` parameter, so there's no
  sampling control and answers can vary slightly run to run.
- `query_orders` has no row limit — a broad `SELECT *` returns all 200 rows
  into the model's context. Fine here; would need a cap at a larger scale.
- No retry on transient API failures — a rate limit or network error surfaces
  to the user as an error message instead of being retried automatically.
- The backend is on Render's free tier, so it cold-starts after idle periods
  (see Live demo above). This is a conscious cost tradeoff for this project,
  not an oversight.
