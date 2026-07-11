"use client";

/**
 * Single chat page. Session-only React state — no auth, no persisted history.
 *
 * POSTs to `${NEXT_PUBLIC_API_URL}/chat` and reads the body as an SSE stream.
 * Events: `tool` (badge + expandable detail: generated SQL or retrieved
 * sources), `token` (append to streaming answer), `citations` (structured
 * source_file + section_title list rendered under the message — never parsed
 * out of the answer prose), `done`.
 */

import { useEffect, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Citation = { source_file: string; section_title?: string | null };
type ToolCall = { tool: string; summary: string; detail: Record<string, unknown> };
type Message = {
  role: "user" | "assistant";
  text: string;
  tools: ToolCall[];
  citations: Citation[];
  streaming?: boolean;
  error?: boolean;
};

const TOOL_LABELS: Record<string, string> = {
  search_documents: "document RAG",
  query_orders: "text-to-SQL",
};

function parseSSE(buffer: string): { events: { event: string; data: string }[]; rest: string } {
  const events: { event: string; data: string }[] = [];
  // sse-starlette emits \r\n line endings — split on both CRLF and LF
  const parts = buffer.split(/\r?\n\r?\n/);
  const rest = parts.pop() ?? "";
  for (const part of parts) {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of part.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length) events.push({ event, data: dataLines.join("\n") });
  }
  return { events, rest };
}

export default function ChatWindow() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const updateLast = (fn: (m: Message) => Message) =>
    setMessages((ms) => [...ms.slice(0, -1), fn(ms[ms.length - 1])]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    setMessages((ms) => [
      ...ms,
      { role: "user", text, tools: [], citations: [] },
      { role: "assistant", text: "", tools: [], citations: [], streaming: true },
    ]);

    try {
      const res = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, rest } = parseSSE(buffer);
        buffer = rest;
        for (const { event, data } of events) {
          const payload = JSON.parse(data);
          if (event === "tool") {
            updateLast((m) => ({ ...m, tools: [...m.tools, payload as ToolCall] }));
          } else if (event === "token") {
            updateLast((m) => ({ ...m, text: m.text + (payload.text as string) }));
          } else if (event === "citations") {
            updateLast((m) => ({ ...m, citations: payload.citations as Citation[] }));
          } else if (event === "error") {
            updateLast((m) => ({
              ...m,
              error: true,
              text: m.text || (payload.message as string),
            }));
          }
        }
      }
      updateLast((m) => ({ ...m, streaming: false }));
    } catch (err) {
      updateLast((m) => ({
        ...m,
        streaming: false,
        error: true,
        text: m.text || `Something went wrong talking to the backend: ${String(err)}`,
      }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={styles.page}>
      <h1 style={styles.title}>Northwind Gadgets — Support Chat</h1>
      <div style={styles.list}>
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              ...styles.msg,
              ...(m.role === "user" ? styles.user : styles.assistant),
              ...(m.error ? styles.error : {}),
            }}
          >
            {m.tools.length > 0 && (
              <div style={styles.badges}>
                {m.tools.map((t, j) => (
                  <span key={j} style={styles.badge}>
                    used: {TOOL_LABELS[t.tool] ?? t.tool}
                  </span>
                ))}
              </div>
            )}
            <div style={styles.text}>
              {m.text || (m.streaming ? "…" : "")}
            </div>
            {(m.citations.length > 0 || m.tools.some((t) => t.detail?.sql)) && (
              <details style={styles.details}>
                <summary style={styles.summary}>sources & SQL</summary>
                {m.citations.length > 0 && (
                  <ul style={styles.ul}>
                    {m.citations.map((c, j) => (
                      <li key={j}>
                        {c.source_file}
                        {c.section_title ? ` — ${c.section_title}` : ""}
                      </li>
                    ))}
                  </ul>
                )}
                {m.tools
                  .filter((t) => t.detail?.sql)
                  .map((t, j) => (
                    <pre key={j} style={styles.sql}>{String(t.detail.sql)}</pre>
                  ))}
              </details>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <form
        style={styles.form}
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          style={styles.input}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about policies or orders…"
          disabled={busy}
        />
        <button style={styles.button} disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </main>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: { maxWidth: 760, margin: "0 auto", padding: 16, display: "flex", flexDirection: "column", height: "100vh", fontFamily: "system-ui, sans-serif" },
  title: { fontSize: 18, margin: "8px 0 16px" },
  list: { flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 12, paddingBottom: 12 },
  msg: { borderRadius: 10, padding: "10px 14px", maxWidth: "85%", whiteSpace: "pre-wrap" },
  user: { alignSelf: "flex-end", background: "#2563eb", color: "#fff" },
  assistant: { alignSelf: "flex-start", background: "#f1f5f9", color: "#111" },
  error: { background: "#fef2f2", color: "#991b1b", border: "1px solid #fecaca" },
  badges: { display: "flex", gap: 6, marginBottom: 6, flexWrap: "wrap" },
  badge: { fontSize: 11, background: "#e2e8f0", color: "#334155", borderRadius: 999, padding: "2px 8px" },
  text: { fontSize: 14, lineHeight: 1.5 },
  details: { marginTop: 8, fontSize: 12, color: "#475569" },
  summary: { cursor: "pointer" },
  ul: { margin: "6px 0", paddingLeft: 18 },
  sql: { background: "#0f172a", color: "#e2e8f0", padding: 8, borderRadius: 6, overflowX: "auto", fontSize: 11 },
  form: { display: "flex", gap: 8, paddingTop: 8, borderTop: "1px solid #e2e8f0" },
  input: { flex: 1, padding: "10px 12px", borderRadius: 8, border: "1px solid #cbd5e1", fontSize: 14 },
  button: { padding: "10px 16px", borderRadius: 8, border: "none", background: "#2563eb", color: "#fff", cursor: "pointer" },
};
