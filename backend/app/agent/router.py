"""
The agent loop. This IS the router — there is no separate classifier: Claude
decides per turn whether to call `search_documents`, `query_orders`, both, or
answer directly.

run(message) is an async generator yielding SSE-ready events:
- ("tool", {"tool", "summary", "detail"})  each time a tool actually runs
- ("token", {"text": ...})             streamed answer tokens
- ("citations", {"citations": [...]})  structured citations, if any

Every turn is a single streaming API call (ROADMAP §4.1 — no hidden
non-streaming decision pass followed by a second generation). The first
`content_block_start` event types the turn: `tool_use` → accumulate
silently and execute; `text` → relay deltas to the client as they arrive.

Citations are id-resolved (ROADMAP §4.2): the model ends its answer with a
`CITATIONS: [<id>, ...]` line naming doc_chunks ids; the stream filter
strips that line from the token events, and each id is resolved against
the chunks `search_documents` actually returned earlier in this run — the
real source_file/section_title come from that lookup, never from the
model's prose. Ids that don't resolve are dropped silently.

Iterations are capped at MAX_TOOL_TURNS; past the cap the model is forced
to answer with whatever evidence it has (tool_choice "none").
"""

import json
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from app.agent import tools
from app.agent.prompts import build_system_prompt
from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from app.db import get_orders_schema

MAX_TOOL_TURNS = 4
MAX_TOKENS = 1024
CITATIONS_MARKER = "CITATIONS:"

_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


def _run_tool(
    name: str, tool_input: dict, retrieved: dict[int, dict]
) -> tuple[Any, dict]:
    """Execute one tool call. Returns (result_for_model, tool_event_detail).

    search_documents results are recorded into `retrieved` (id -> source
    metadata) so the citation resolver can validate the model's cited ids
    against what was actually returned this run.
    """
    if name == "search_documents":
        result = tools.search_documents(tool_input["query"])
        sources = []
        for chunk in result:
            retrieved[chunk["id"]] = {
                "source_file": chunk["source_file"],
                "section_title": chunk["section_title"],
            }
            sources.append(
                {
                    "id": chunk["id"],
                    "source_file": chunk["source_file"],
                    "section_title": chunk["section_title"],
                }
            )
        return result, {"sources": sources}
    if name == "query_orders":
        result = tools.query_orders(tool_input["sql"])
        detail = {"sql": tool_input["sql"]}
        if "error" in result:
            detail["error"] = result["error"]
        else:
            detail["row_count"] = len(result["rows"])
        return result, detail
    return {"error": f"Unknown tool: {name}"}, {"error": f"Unknown tool: {name}"}


def _summarize(name: str, detail: dict) -> str:
    if name == "search_documents":
        files = sorted({s["source_file"] for s in detail.get("sources", [])})
        return f"document RAG — retrieved from {', '.join(files)}"
    if "error" in detail:
        return f"text-to-SQL — rejected: {detail['error']}"
    return f"text-to-SQL — {detail.get('row_count', 0)} row(s)"


def _resolve_citations(raw_tail: str, retrieved: dict[int, dict]) -> list[dict]:
    """Parse the CITATIONS tail and resolve ids against actually-retrieved
    chunks. Unresolvable or malformed ids are dropped, never trusted."""
    try:
        cited = json.loads(raw_tail[len(CITATIONS_MARKER):].strip())
    except json.JSONDecodeError:
        return []
    if not isinstance(cited, list):
        return []
    resolved, seen = [], set()
    for chunk_id in cited:
        try:
            chunk_id = int(chunk_id)
        except (TypeError, ValueError):
            continue
        meta = retrieved.get(chunk_id)
        if meta is None:
            continue  # model cited an id it was never given — drop it
        key = (meta["source_file"], meta["section_title"])
        if key not in seen:
            seen.add(key)
            resolved.append(meta)
    return resolved


async def run(message: str) -> AsyncIterator[tuple[str, dict]]:
    system = build_system_prompt(get_orders_schema())
    messages: list[dict] = [{"role": "user", "content": message}]
    retrieved: dict[int, dict] = {}  # doc_chunks.id -> {source_file, section_title}
    final_citations_tail: str | None = None
    relayed_any = False

    for turn in range(MAX_TOOL_TURNS + 1):
        force_answer = turn == MAX_TOOL_TURNS
        relay: bool | None = None  # decided by the first content_block_start
        pending = ""
        citations_tail: str | None = None  # per-turn CITATIONS capture

        # No temperature pin: claude-sonnet-5 rejects any explicit sampling
        # parameter with 400 "`temperature` is deprecated for this model" —
        # verified empirically BOTH with adaptive thinking active and with
        # thinking={"type": "disabled"} (it is not the older thinking-gated
        # restriction; the parameter is removed on this model generation).
        # Run-to-run variance can't be reduced via sampling params on this
        # tier. Noted in README limitations.
        # Thinking is explicitly disabled: sonnet-5 runs adaptive thinking by
        # default, whose thinking blocks would eat the max_tokens budget and
        # open turns with a non-text block; this routing workload doesn't
        # need it.
        async with _client.messages.stream(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "disabled"},
            system=system,
            tools=tools.TOOL_DEFS,
            tool_choice={"type": "none"} if force_answer else {"type": "auto"},
            messages=messages,
        ) as stream:
            async for event in stream:
                if event.type == "content_block_start" and relay is None:
                    # First non-thinking block types the turn: text → relay,
                    # tool_use → accumulate silently and execute (ROADMAP
                    # §4.1). Thinking blocks never decide the turn — if they
                    # did, an adaptive-thinking turn would lock relay=False
                    # and silently swallow the answer.
                    if event.content_block.type == "thinking":
                        continue
                    relay = event.content_block.type == "text"
                    if relay and relayed_any:
                        # A previous turn already relayed preamble text —
                        # separate it visually from this turn's text.
                        yield "token", {"text": "\n\n"}
                elif (
                    event.type == "content_block_delta"
                    and event.delta.type == "text_delta"
                    and relay
                ):
                    text = event.delta.text
                    if citations_tail is not None:
                        citations_tail += text
                        continue
                    pending += text
                    marker_at = pending.find(CITATIONS_MARKER)
                    if marker_at != -1:
                        head = pending[:marker_at].rstrip()
                        if head:
                            yield "token", {"text": head}
                            relayed_any = True
                        citations_tail = pending[marker_at:]
                        pending = ""
                    else:
                        # Flush all but a tail long enough to hide a marker
                        # split across delta boundaries.
                        safe = len(pending) - len(CITATIONS_MARKER)
                        if safe > 0:
                            yield "token", {"text": pending[:safe]}
                            relayed_any = True
                            pending = pending[safe:]
            response = await stream.get_final_message()

        if relay and citations_tail is None and pending:
            yield "token", {"text": pending}
            relayed_any = True
            pending = ""
        if citations_tail is not None:
            final_citations_tail = citations_tail

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in tool_uses:
            result, detail = _run_tool(block.name, block.input, retrieved)
            yield "tool", {
                "tool": block.name,
                "summary": _summarize(block.name, detail),
                "detail": detail,
            }
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                }
            )
        messages.append({"role": "user", "content": results})

    if final_citations_tail is not None:
        citations = _resolve_citations(final_citations_tail, retrieved)
        if citations:
            yield "citations", {"citations": citations}
