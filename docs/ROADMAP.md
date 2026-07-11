# Roadmap — Dual-Mode Agentic RAG Chatbot (Northwind Gadgets)

**This file replaces `ARCHITECTURE.md`, `TASKS.md`, and `CLAUDE_CODE_PLAYBOOK.md`.**
Point Claude Code only at this file going forward — the other three are now
stale and should be ignored/deleted in your project to avoid conflicting
instructions.

Anything in this doc marked **[decided]** is locked — don't relitigate it
without saying so explicitly. Anything marked **[open]** is a real unresolved
question — ask before assuming.

## 0. What the brief actually requires

Build one chatbot for a fictional company (Northwind Gadgets) that answers
questions from two sources and decides which to use per question:
- Document questions → vector RAG with citations
- Data questions → generated SQL against an `orders` table, executed live
  (never embedded for vector search)
- Mixed questions → both, in the same turn
- Out-of-scope questions → "I don't have that information," no fabrication

Constraints: FastAPI + token-level streaming, any LLM provider, Next.js
frontend showing which tool fired + the citation/SQL, candidate's choice of
vector store, Dockerised, public GitHub repo + README, live public URL
mandatory. Graded against a fixed dataset "so submissions can be graded
consistently against the same ground truth" — treat the dataset as fixed,
don't edit it.

## 1. Decisions log — everything not dictated by the brief

| Decision | Choice | Status |
|---|---|---|
| LLM | Claude, native tool use | **[decided]** — implemented |
| Embeddings | Local `sentence-transformers` (all-MiniLM-L6-v2) | **[decided]** — implemented |
| Vector store | Postgres + pgvector, same DB as `orders` | **[decided]** — implemented |
| Orchestration | Hand-rolled tool-use loop, max 4 turns, no framework | **[decided]** — implemented |
| SQL validation | `sqlglot` parse: single SELECT, `orders` table only, live-schema columns only, no CTEs, no multi-statement | **[decided]** — implemented, 9/9 adversarial tests pass |
| Chunking | One chunk per numbered/bold section heading, not token windows | **[decided]** — implemented, 22 chunks across 5 docs |
| Citations | Model cites by `doc_chunks.id`, backend resolves against ids it actually returned this turn, unresolvable ids dropped silently | **[decided]** — built, but as of the last review still self-reports filenames as free text, not yet id-validated. Fix now (§4) |
| Deployment | Render (backend + Postgres, **paid Starter tier, not free**) + Vercel (frontend) | **[decided]** — not started. See §5 for why paid, not free |
| Model tier | `claude-sonnet-5`, not Opus | **[decided]** — currently defaults to `claude-opus-4-8` in `config.py`, change it. Tool routing + short-context RAG + narrow SQL generation doesn't need frontier-tier reasoning, and it compounds the double-generation bug below (up to 2x calls per turn on the most expensive tier) |
| Primary language | Python for everything except the UI | **[decided], confirmed]** — Next.js frontend stays because the brief explicitly requires it as a technical constraint, not a stylistic choice; "Python-first" applies to backend/agent/ingestion, which already is 100% Python |

If you want to change any row marked "implemented," say so explicitly — it's
rework, not a free edit.

## 2. Dataset facts (Northwind Gadgets, INR pricing)

**`orders` table** (200 rows, loaded as-is, do not edit):
```
order_id, customer, product, amount, status, order_date
```
No `delivery_date`, no `quantity` column — these do not exist and must not
be invented. Two rows break the `ORD-1NNN` pattern (`ORD-1207`, `ORD-1233`)
— exact-match lookups only, don't assume ID format.

**Five source docs** (hr_leave_policy, pricing_discounts_policy,
product_faq, returns_policy, warranty_policy — all short, numbered
sections). Returns policy §4 and the separate warranty policy cross-
reference each other — a single warranty question can legitimately need
citations from both files.

**The graded trap**: the brief's own example mixed question — "did order
1234 qualify?" — uses an order that does not exist in the data, and even
for orders that do exist, exact eligibility can't be computed without
`delivery_date`. The system prompt must state these gaps explicitly rather
than approximate silently. This is very likely the actual thing being
scored under "no hallucinated SQL columns."

## 3. Build status (update this table as you go — this is the single place
that reflects reality)

| Step | What | Status |
|---|---|---|
| 1 | `db.py` + `load_orders.py` — 200 rows loaded, typo rows intact | ✅ done |
| 2 | `ingest_docs.py` — 22 chunks, section-based | ✅ done |
| 3 | `sql_guard.py` — sqlglot, 9/9 adversarial tests | ✅ done |
| 4 | `tools.py` — search_documents / query_orders | ✅ done |
| 5 | `prompts.py` + `router.py` — tool-use loop | ✅ done — §4.1 fixed: one streaming call per turn, first `content_block_start` types the turn (tool_use → accumulate+execute, text → relay deltas); no second generation, no discarded decision |
| 5b | Citation-by-id resolution | ✅ done — §4.2 fixed: `search_documents` returns `doc_chunks.id`, model cites `CITATIONS: [<id>, ...]`, router resolves ids against this run's retrieved set; unresolvable/invented ids dropped (mock test proves id 99 dropped) |
| 6 | `.env` / API key | ✅ done |
| 6b | Live agent tests (`backend/tests/test_brief.py`) | ✅ re-run after §4.1/§4.2 landed, against the dockerized backend on `claude-sonnet-5`: 10/10 checks pass incl. "did order 1234 qualify" (not-found, no fabrication) and two-file warranty citation (returns_policy.pdf + warranty_policy.pdf, id-resolved) |
| 7 | `main.py` FastAPI `/chat` SSE endpoint | ✅ done — §4.3 fixed: exceptions in `event_stream()` logged server-side and emitted as `error` SSE event (verified end-to-end with a bogus model → client received `event: error` then `done`); ChatWindow renders it in a red error bubble instead of leaving it blank |
| 8 | Frontend `ChatWindow.tsx` | ✅ built, SSE parsing correct — not yet manually clicked through in a browser |
| 9 | `.dockerignore` (backend + frontend) | ✅ done — backend context 63kB (was shipping 1.2GB `.venv`), frontend 27kB; no-change rebuild fully CACHED in ~2s |
| 9b | Full `docker compose build` clean, end-to-end test | ✅ done — `--no-cache` build of both images clean; `test_brief.py` run twice back-to-back through the dockerized stack: 10/10 both passes. Found+fixed en route: sonnet-5's adaptive thinking opens turns with a `thinking` block, which locked the relay decision to "silent" and swallowed answers intermittently — thinking now explicitly disabled and relay decision skips thinking blocks (mock regression test added, 7/7) |
| 10 | Deploy: Render (backend+db, paid tier) + Vercel (frontend) | 🔲 not started |
| 11 | README finalized from this doc | ✅ done — dead ARCHITECTURE/TASKS links → ROADMAP.md, per-turn streaming described correctly, model named (`claude-sonnet-5`), citation guarantee stated precisely (id-resolved against retrieved set; marker parsed from stream but filenames never self-reported), limitations added (no sampling control, no query LIMIT, no retry), test scripts referenced instead of manual checklist. Live-URL still TODO pending step 10 |
| 12 | Push to public GitHub repo, confirm live URL from a clean browser | 🔲 not started |

This table was significantly out of date as of the last review — §7/§8 were marked "not started" here but were actually already built. Keep this table honest by editing it in the same commit as the code change, not after.

## 4. What to do next, in order

### 4.1 Fix: `router.py` generates every answer twice

Currently (see `run()`): the tool-decision loop calls `messages.create`
non-streaming each turn; the moment a turn returns no `tool_use` blocks, that
turn's actual text is discarded (`break`, `response.content` unused) and the
*entire* message history is re-sent to `messages.stream()` for a second,
independently-sampled generation. This happens on every request, not just
the tool-cap edge case. Effects: latency to first token roughly doubles (a
full hidden generation completes before the "streamed" one starts), and
because temperature isn't pinned to 0, the discarded decision and the shown
answer can disagree — the second call also forces `tool_choice="none"`, so
it structurally can't call a tool even if that fresh sample wanted to.

Fix: stream every turn from the start. Anthropic's streaming API emits a
`content_block_start` event typed before the block's content arrives — if
the first block is `tool_use`, accumulate silently and execute the tool as
today; if it's `text`, relay the deltas to the client as they arrive. One
API call per turn, no wasted generation, no divergence between what was
decided and what's shown.

### 4.2 Fix: citation-by-id, not self-reported filenames

`tools.py`'s `search_documents` doesn't return a chunk id at all, and
`prompts.py` still instructs the model to write `source_file`/`section_title`
directly into the `CITATIONS:` block as free text — nothing cross-checks
that against what was actually retrieved this turn. This is the same
"trust the model's transcription of a structured fact" problem the SQL
guard exists to prevent, just not yet applied to citations.

Fix: `search_documents` returns each chunk tagged with its `doc_chunks.id`.
Prompt instructs the model to cite by id (`CITATIONS: [4, 7]`), not
filename. Router resolves each id against the set of ids actually returned
by `search_documents` earlier in the same turn — the real `source_file`/
`section_title` come from that lookup, not from the model's text. Any id
that doesn't resolve is dropped silently, not trusted.

Do 4.1 and 4.2 together — they're both inside `run()` and restructuring the
streaming loop twice would be wasted churn.

### 4.3 Fix: `main.py` has no error handling

If the Anthropic call fails mid-stream (rate limit, bad key, transient
network error), `event_stream()` has nothing catching it — the SSE
connection just dies. The frontend's catch block only fires on network-level
fetch errors, so the user sees an empty assistant bubble with no
explanation. Add an `error` SSE event type, catch exceptions in
`event_stream()`, emit `error` with a short message, and have the frontend
render it instead of leaving the bubble blank.

### 4.4 Fix: no `.dockerignore` anywhere

`backend/.venv` (1.2GB, mostly torch) and `frontend/node_modules` + `.next`
(~280MB) have no exclusion from the Docker build context in either service.
Every `docker build` ships all of that as context, and `COPY . .`
invalidates the layer cache whenever anything inside `.venv` changes — this
is almost certainly the actual cause of the slow build. Add
`backend/.dockerignore` (`.venv/`, `__pycache__/`, `.gstack/`) and
`frontend/.dockerignore` (`node_modules/`, `.next/`).

### Then, in order

5. Re-run `backend/tests/test_brief.py` for real against the now-fixed
   router — do not trust any test run from before 4.1/4.2 landed, the
   behavior changed. All 7 checks must pass, including "did order 1234
   qualify" (verbatim from the brief) and the two-file citation cite.
6. Manually click through the same six questions in a browser against the
   live backend — a clean `next build` is not the same as a working UI.
7. `docker compose build --no-cache`, full stack up, re-run the six
   questions through Docker specifically (this is what catches env/
   build-arg issues dev servers hide).
8. Deploy. Render backend+Postgres on a **paid Starter tier** — free tier
   Postgres self-deletes after 30 days and free web services cold-start
   30-60s after 15 min idle, both unacceptable for a "live URL is mandatory"
   deliverable with streaming as a graded requirement. Vercel frontend on
   its default hobby build. Confirm CORS on the backend explicitly allows
   the deployed Vercel domain.
9. Write the final README from this file (trim it, don't paste verbatim) —
   architecture, choices + reasoning, routing explanation, known
   limitations (delivery_date/quantity gap, local-embeddings tradeoff, the
   two dataset ID typos, and — honestly — that the deployed frontend runs
   via Vercel's native build rather than `frontend/Dockerfile`). Also fix
   the current README line that overstates the citation guarantee ("never
   parsed out of the answer prose") — true after 4.2, not before.
10. Push to a public GitHub repo. Open the live URL in a private/incognito
    window and run the six questions one more time before calling it done.

## 5. Known limitations to state in the README, not hide

- Local embeddings trade retrieval recall for zero external dependency —
  fine at this corpus size (22 chunks), wouldn't scale as-is.
- `orders` has no `delivery_date`/`quantity` — return/warranty-window and
  bulk-discount questions are answered approximately or declared out of
  scope, by design.
- SQL guard is a narrow allowlist over one table — no joins, no general
  text-to-SQL surface, intentional.
- No conversation memory beyond a single turn's tool-use loop.
- The deployed frontend is built natively by Vercel, not from
  `frontend/Dockerfile` — the Dockerfile is accurate for local `docker
  compose` use, but isn't literally what's running in production.
