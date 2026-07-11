"""
Unit test for router.run()'s per-turn streaming loop — no LLM, no network.
Mocks the Anthropic streaming client and exercises:
- the CITATIONS marker filter (including a marker split across deltas)
- citation-by-id resolution against actually-retrieved chunk ids
  (unresolvable ids dropped, never trusted)
- a tool turn (first block tool_use, accumulated silently) followed by a
  relayed text turn

Run: python -m tests.test_stream_filter
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.agent import router


def _text_events(chunks: list[str]):
    """Stream events for a single text block delivered in given deltas."""
    events = [
        SimpleNamespace(
            type="content_block_start",
            content_block=SimpleNamespace(type="text"),
        )
    ]
    for c in chunks:
        events.append(
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text=c),
            )
        )
    return events


def _tool_events():
    """Stream events for a tool_use turn (no text relayed)."""
    return [
        SimpleNamespace(
            type="content_block_start",
            content_block=SimpleNamespace(type="tool_use"),
        )
    ]


def _mixed_events(chunks: list[str]):
    """Text block (relayed preamble) followed by a tool_use block start in
    the SAME turn — the model wrote text before deciding to call a tool."""
    return _text_events(chunks) + [
        SimpleNamespace(
            type="content_block_start",
            content_block=SimpleNamespace(type="tool_use"),
        )
    ]


class FakeStream:
    def __init__(self, events, final_message):
        self._events = events
        self._final = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()

    async def get_final_message(self):
        return self._final


def fake_client(turns: list[FakeStream]):
    it = iter(turns)
    return SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: next(it)))


def text_final():
    return SimpleNamespace(content=[SimpleNamespace(type="text", text="x")])


def tool_final(name: str, tool_input: dict):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="tool_use", name=name, input=tool_input, id="tu_1")
        ]
    )


def mixed_final(name: str, tool_input: dict):
    """Final message for a turn containing both preamble text and a tool call."""
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="preamble"),
            SimpleNamespace(type="tool_use", name=name, input=tool_input, id="tu_1"),
        ]
    )


async def collect(turns, search_result=None):
    events = []
    patches = [
        patch.object(router, "_client", fake_client(turns)),
        patch.object(router, "get_orders_schema", lambda: {"order_id": "text"}),
    ]
    if search_result is not None:
        patches.append(
            patch.object(router.tools, "search_documents", lambda q: search_result)
        )
    with patches[0], patches[1]:
        if search_result is not None:
            with patches[2]:
                async for ev in router.run("q"):
                    events.append(ev)
        else:
            async for ev in router.run("q"):
                events.append(ev)
    return events


def run_case(name, turns, want_text, want_citations, search_result=None, want_tool=None):
    events = asyncio.run(collect(turns, search_result))
    text = "".join(p["text"] for t, p in events if t == "token")
    cites = next((p["citations"] for t, p in events if t == "citations"), [])
    tools_fired = [p["tool"] for t, p in events if t == "tool"]
    ok = text == want_text and cites == want_citations
    if want_tool is not None:
        ok = ok and want_tool in tools_fired
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"   text={text!r}\n   cites={cites!r}\n   tools={tools_fired!r}")
    return ok


CHUNKS = [
    {"id": 4, "chunk_text": "t", "source_file": "returns_policy.pdf", "section_title": "1. Return Window"},
    {"id": 17, "chunk_text": "t", "source_file": "warranty_policy.pdf", "section_title": "1. Coverage Period"},
]


def main():
    results = [
        run_case(
            "plain text, marker in one delta",
            [FakeStream(_text_events(["The window is 30 days.\n", "CITATIONS: [4]"]), text_final())],
            "The window is 30 days.",
            [],  # id 4 never retrieved this run -> dropped
        ),
        run_case(
            "tool turn then answer, ids resolved",
            [
                FakeStream(_tool_events(), tool_final("search_documents", {"query": "q"})),
                FakeStream(_text_events(["Answer.", "\nCITA", "TIONS: [4, 17, 99]"]), text_final()),
            ],
            "Answer.",
            [
                {"source_file": "returns_policy.pdf", "section_title": "1. Return Window"},
                {"source_file": "warranty_policy.pdf", "section_title": "1. Coverage Period"},
            ],  # 99 never retrieved -> dropped
            search_result=CHUNKS,
        ),
        run_case(
            "no citations line",
            [FakeStream(_text_events(["I don't have ", "that information."]), text_final())],
            "I don't have that information.",
            [],
        ),
        run_case(
            "tiny deltas",
            [FakeStream(_text_events(list("Hello world.\nCITATIONS: []")), text_final())],
            "Hello world.",
            [],
        ),
        run_case(
            "malformed citations json dropped",
            [FakeStream(_text_events(["Hi.\n", "CITATIONS: [not json"]), text_final())],
            "Hi.",
            [],
        ),
        run_case(
            # Regression: a turn that OPENS with a thinking block (adaptive
            # thinking) must not lock relay=False — the answer text after it
            # must still stream. This silently swallowed answers in prod.
            "thinking block first, then text — answer still relayed",
            [
                FakeStream(
                    [
                        SimpleNamespace(
                            type="content_block_start",
                            content_block=SimpleNamespace(type="thinking"),
                        )
                    ]
                    + _text_events(["The answer after thinking."]),
                    text_final(),
                )
            ],
            "The answer after thinking.",
            [],
        ),
        run_case(
            # Regression for the prompt's "call tools before writing text"
            # rule: model leaks preamble text, then calls a tool in the SAME
            # turn. Tool must still execute; the final answer follows the
            # leaked preamble after a blank-line separator (relayed_any).
            "mixed turn: preamble text then tool_use in same turn",
            [
                FakeStream(
                    _mixed_events(["Let me check that."]),
                    mixed_final("search_documents", {"query": "q"}),
                ),
                FakeStream(_text_events(["The answer.", "\nCITATIONS: [4]"]), text_final()),
            ],
            "Let me check that.\n\nThe answer.",
            [{"source_file": "returns_policy.pdf", "section_title": "1. Return Window"}],
            search_result=CHUNKS,
            want_tool="search_documents",
        ),
    ]
    print(f"{sum(results)}/{len(results)} passed")
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
