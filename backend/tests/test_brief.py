"""
End-to-end tests against a live backend: document, data, and mixed
questions, out-of-scope fallback, and the dataset's specific edge cases.

Usage: python -m tests.test_brief [base_url]  (default http://localhost:8000)

Prints each question, the tool events, the answer, and the citations, plus a
PASS/FAIL line per scripted expectation. Needs ANTHROPIC_API_KEY set on the
backend — this exercises the real agent loop end to end.
"""

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def ask(message: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}/chat",
        data=json.dumps({"message": message}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    tools, answer, citations = [], "", []
    with urllib.request.urlopen(req, timeout=120) as resp:
        event = None
        for raw in resp:
            line = raw.decode().rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event:
                data = json.loads(line[5:].strip() or "{}")
                if event == "tool":
                    tools.append(data)
                elif event == "token":
                    answer += data["text"]
                elif event == "citations":
                    citations = data["citations"]
    return {"tools": tools, "answer": answer, "citations": citations}


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return ok


def main() -> None:
    results = []

    print("=" * 70)
    q = "What is the refund window?"
    r = ask(q)
    print(f"Q: {q}\nTOOLS: {[t['tool'] for t in r['tools']]}\nA: {r['answer']}\nCITES: {r['citations']}\n")
    results.append(check("doc question cites a source", bool(r["citations"])))
    results.append(check(
        "doc question used document RAG",
        any(t["tool"] == "search_documents" for t in r["tools"]),
    ))

    print("=" * 70)
    q = "Is a defective item covered after 30 days?"
    r = ask(q)
    print(f"Q: {q}\nTOOLS: {[t['tool'] for t in r['tools']]}\nA: {r['answer']}\nCITES: {r['citations']}\n")
    cited_files = {c["source_file"] for c in r["citations"]}
    results.append(check(
        "cites BOTH returns_policy.pdf and warranty_policy.pdf",
        {"returns_policy.pdf", "warranty_policy.pdf"} <= cited_files,
        str(cited_files),
    ))

    print("=" * 70)
    q = "How many orders are pending?"
    r = ask(q)
    print(f"Q: {q}\nTOOLS: {[t['tool'] for t in r['tools']]}\nA: {r['answer']}\nCITES: {r['citations']}\n")
    sqls = [t["detail"].get("sql") for t in r["tools"] if t["tool"] == "query_orders"]
    results.append(check("data question ran SQL", bool(sqls), str(sqls)))
    results.append(check("answer contains the true count (24)", "24" in r["answer"]))

    print("=" * 70)
    q = "Was order ORD-1002 eligible for a return under the 30-day policy?"
    r = ask(q)
    print(f"Q: {q}\nTOOLS: {[t['tool'] for t in r['tools']]}\nA: {r['answer']}\nCITES: {r['citations']}\n")
    used = {t["tool"] for t in r["tools"]}
    results.append(check("mixed question fired both tools", used >= {"search_documents", "query_orders"}, str(used)))
    results.append(check(
        "mixed answer flags delivery_date not tracked / approximation",
        any(w in r["answer"].lower() for w in ["not tracked", "isn't tracked", "is not tracked", "don't track", "no delivery", "delivery date isn"]),
    ))

    print("=" * 70)
    q = "did order 1234 qualify for a return"  # order 1234 doesn't exist in the data
    r = ask(q)
    print(f"Q: {q}\nTOOLS: {[t['tool'] for t in r['tools']]}\nA: {r['answer']}\nCITES: {r['citations']}\n")
    results.append(check(
        "order 1234 -> not-found/fallback, not fabricated",
        any(w in r["answer"].lower() for w in ["not found", "no order", "doesn't exist", "does not exist", "couldn't find", "could not find", "don't have that information"]),
    ))

    print("=" * 70)
    q = "What is the status of order ORD-1207?"
    r = ask(q)
    print(f"Q: {q}\nTOOLS: {[t['tool'] for t in r['tools']]}\nA: {r['answer']}\nCITES: {r['citations']}\n")
    results.append(check("pattern-breaking ORD-1207 exact match works", "returned" in r["answer"].lower()))

    print("=" * 70)
    q = "Who won the cricket world cup in 2023?"
    r = ask(q)
    print(f"Q: {q}\nTOOLS: {[t['tool'] for t in r['tools']]}\nA: {r['answer']}\nCITES: {r['citations']}\n")
    results.append(check(
        "out-of-scope -> exact fallback string",
        r["answer"].strip() == "I don't have that information.",
        repr(r["answer"].strip()),
    ))

    print("=" * 70)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
