import { useRef, useState } from "react";
import { Send, Sparkles, BookOpen } from "lucide-react";
import { streamChat, type ChatEvent } from "../api";
import { Badge } from "../ui";

interface Msg { role: "user" | "assistant"; text: string; citations?: string[]; runs?: number; error?: boolean }

const SUGGESTIONS = [
  "What's the current state of the pipeline?",
  "How does the second-wave continuation model work?",
  "Why are 6 hypotheses structurally dead?",
  "Explain order-flow imbalance and how we use it.",
];

export function ChatView() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  function scroll() {
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: 1e9, behavior: "smooth" }));
  }

  async function send(q: string) {
    const query = q.trim();
    if (!query || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", text: query }, { role: "assistant", text: "" }]);
    setBusy(true);
    scroll();
    const patchLast = (fn: (m: Msg) => Msg) =>
      setMsgs((arr) => arr.map((m, i) => (i === arr.length - 1 ? fn(m) : m)));
    try {
      await streamChat(query, (e: ChatEvent) => {
        if (e.type === "context") patchLast((m) => ({ ...m, citations: e.rag_titles, runs: e.runs }));
        else if (e.type === "token") patchLast((m) => ({ ...m, text: m.text + (e.text ?? "") }));
        else if (e.type === "error") patchLast((m) => ({ ...m, text: m.text + `\n\n⚠ ${e.detail}`, error: true }));
        scroll();
      });
    } catch (err) {
      patchLast((m) => ({ ...m, text: m.text + `\n\n⚠ ${String(err)}`, error: true }));
    } finally {
      setBusy(false); scroll();
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col">
      <div className="mb-3 flex items-center gap-2">
        <Sparkles size={18} className="text-accent" />
        <h2 className="text-base font-semibold">Assistant</h2>
        <Badge tone="accent">local Gemma</Badge>
        <Badge tone="dim">system-state aware</Badge>
        <Badge tone="dim">vault-grounded</Badge>
      </div>

      <div ref={scrollRef} className="scroll-area flex-1 space-y-4 overflow-auto pr-1">
        {msgs.length === 0 && (
          <div className="panel p-5">
            <div className="mb-3 text-sm text-ink-dim">Ask about the system, a model, the backtests, or the theory. Read-only — it explains, it never acts.</div>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)} className="chip hover:border-accent/60 hover:text-accent">{s}</button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
            <div className={`max-w-[85%] rounded-xl2 px-4 py-3 text-sm leading-relaxed ${m.role === "user" ? "bg-accent/15 border border-accent/30" : "panel"} ${m.error ? "border-bad/40" : ""}`}>
              {m.citations && m.citations.length > 0 && (
                <div className="mb-2 flex flex-wrap items-center gap-1.5 border-b border-line pb-2">
                  <BookOpen size={12} className="text-ink-faint" />
                  {m.citations.map((c) => <Badge key={c} tone="dim">{c}</Badge>)}
                  {!!m.runs && <Badge tone="accent">{m.runs} runs</Badge>}
                </div>
              )}
              <div className="whitespace-pre-wrap">{m.text || (busy && i === msgs.length - 1 ? "…" : "")}</div>
            </div>
          </div>
        ))}
      </div>

      <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="mt-3 flex gap-2">
        <input value={input} onChange={(e) => setInput(e.target.value)} disabled={busy}
          placeholder="Ask the cockpit…" autoFocus
          className="flex-1 rounded-xl2 border border-line bg-bg-panel px-4 py-3 text-sm outline-none focus:border-accent/60" />
        <button type="submit" disabled={busy || !input.trim()}
          className="grid place-items-center rounded-xl2 border border-accent/40 bg-accent/15 px-4 text-accent disabled:opacity-40">
          <Send size={16} />
        </button>
      </form>
    </div>
  );
}
