# Dual-Mode Agentic RAG Chatbot

A support chatbot for the fictional retailer **Northwind Gadgets** that answers from
two strictly separated knowledge sources:

1. **Documents** — five policy PDFs (HR leave, pricing/discounts, product FAQ,
   returns/refunds, warranty), chunked per section and served via vector search.
2. **Structured data** — an `orders` table (200 rows), queried via generated SQL.

Document questions go through RAG with citations; data questions go through
validated text-to-SQL; mixed questions use both in one agent turn. The structured
data is never embedded, and order facts are never answered from model memory.

## Live demo

- Frontend: https://dual-mode-rag-chatbot.vercel.app
- Backend: https://dual-mode-rag-chatbot.onrender.com (`GET /health`)

Note: the Render backend is on a plan that cold-starts after ~15 min idle —
the first request after a gap can take up to a couple of minutes while the
container spins up and reloads the embedding model. Confirm this is on a
plan that doesn't do this before treating the URL as demo-ready (see
Known limitations).

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

- **LLM**: `claude-sonnet-5` (Anthropic API) with native tool use. The tool-use
  loop *is* the router — no separate classifier, no LangChain. Claude decides per
  turn whether to call `search_documents`, `query_orders`, both, or answer
  directly. **Every turn is a single streaming API call**: the first
  `content_block_start` event types the turn — a `tool_use` block is accumulated
  silently and executed, a `text` block is relayed to the client token-by-token
  as it arrives. No hidden decision pass, no second generation.
- **Embeddings**: local `sentence-transformers` (`all-MiniLM-L6-v2`), computed at
  ingest time and at query time with the same model. Zero extra API cost and
  deterministic; the corpus is only ~22 section chunks, so retrieval-quality ceiling
  is not the bottleneck.
- **Vector store**: Postgres + the `pgvector` extension. One database serves both
  the `doc_chunks` table (vector search) and the `orders` table (plain SQL) —
  minimal infra, and it makes "structured data is not embedded" true by
  construction: they are separate tables with separate one-shot ingestion scripts.
  (Embeddings are passed as text and cast with `::vector` in SQL — the pgvector
  Python adapter is not needed or installed.)
- **Chunking**: one chunk per policy section, split on the documents' own bold
  headings (not fixed token windows).
- **Citations — id-resolved, not self-reported**: `search_documents` returns each
  chunk tagged with its `doc_chunks.id`. The model ends a document-based answer
  with a `CITATIONS: [<id>, ...]` line, which the router strips from the token
  stream and resolves against the set of ids that `search_documents` actually
  returned in that request — the `source_file`/`section_title` shown in the UI
  come from that lookup, never from the model's prose. Ids the model was never
  given are dropped. (The marker line itself is parsed out of the stream; the
  guarantee is that filenames can't be hallucinated, because the model never
  writes them.)

## Guardrails

- **SQL validation** (`app/agent/sql_guard.py`): every generated query is parsed
  with `sqlglot` — exactly one SELECT statement, table must be `orders`, and every
  column must exist in the schema introspected from the live database at request
  time. Not a regex denylist. Queries also execute inside a `READ ONLY` transaction.
- **No hallucinated schema**: the system prompt receives the live introspected
  schema. `orders` has **no `delivery_date` and no `quantity`** column, and the
  prompt says so explicitly — questions that depend on either (return/warranty
  window measured from delivery, bulk-discount qualification by units) are answered
  by stating that data isn't tracked, optionally offering `order_date` as a clearly
  labeled approximation.
- **Fallback**: out-of-scope questions, or questions with no supporting tool
  evidence, get exactly *"I don't have that information."* — no plausible guesses.
- **Fixed date**: the assessment date `2026-06-15` is a prompt constant (never
  `datetime.now()`), so "last month"-style questions resolve consistently against
  the fixed dataset.
- **Exact order lookups**: `order_id` is matched as an exact string; the dataset
  intentionally contains IDs that break the sequential pattern, and nonexistent
  orders (e.g. `ORD-1234`) return "not found" rather than a fabricated answer.
- **Error surfacing**: failures mid-stream (rate limit, network) are caught and
  emitted as an `error` SSE event, which the UI renders — no silent blank bubbles.

## Setup

```bash
cp .env.example .env   # fill in ANTHROPIC_API_KEY
docker compose up --build
# one-time, after `db` is up:
docker compose exec backend python -m app.ingestion.load_orders
docker compose exec backend python -m app.ingestion.ingest_docs
```

Frontend: http://localhost:3000
Backend: http://localhost:8000 (`GET /health`, `POST /chat` — SSE stream of
`tool`, `token`, `citations`, `error`, `done` events)

## Tests

- `backend/tests/test_brief.py` — end-to-end agent tests against a running
  backend (document / data / mixed / out-of-scope questions, plus the
  dataset-specific traps: nonexistent order 1234, the pattern-breaking
  `ORD-1207`, the two-file warranty citation). Run:
  `python -m tests.test_brief http://localhost:8000`
- `backend/tests/test_stream_filter.py` — mocked unit tests for the router's
  streaming loop: CITATIONS marker filtering across delta boundaries,
  citation-id resolution (invented ids dropped), thinking-block and
  mixed-turn regressions. No API key or network needed. Run:
  `python -m tests.test_stream_filter`

## Known limitations

- Local embeddings trade retrieval recall for zero external dependency —
  acceptable at this corpus size, would not scale as-is.
- `orders` has no `delivery_date` or `quantity` column, so return/warranty-window
  and bulk-discount questions can only be answered approximately (from
  `order_date` and status) or declared out of scope — by design, not an oversight.
- No conversation memory beyond the current turn's tool-use loop — each question
  is independent.
- The SQL guard is an allowlist over a single fixed table; it deliberately does
  not support CTEs or other tables — intentionally narrow, not a general
  text-to-SQL surface.
- **No sampling control on `claude-sonnet-5`** — the API rejects `temperature`
  (verified empirically, with thinking both active and disabled), so run-to-run
  output can vary slightly. Low practical impact: the actual source of
  run-to-run variance was an adaptive-thinking streaming bug, already fixed
  (thinking is now explicitly disabled and the relay logic ignores thinking
  blocks).
- **`query_orders` has no result-size cap** — a `SELECT *` over the whole table
  returns all 200 rows into the model's context. Fine at this dataset size,
  would need a LIMIT guard at scale.
- **No retry on transient API errors** — a rate-limit or network failure
  mid-request surfaces as an `error` event to the user rather than being
  retried.
- **Render cold start observed in production** — a request after ~15 min of
  inactivity took roughly two minutes to return (container spin-up + model
  reload), which is a real risk for a "live URL is mandatory" deliverable if
  it's tested unannounced. Verify the Render plan is one that stays warm, or
  document the expected delay explicitly here so it isn't mistaken for a
  broken deployment.
