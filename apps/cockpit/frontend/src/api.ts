import { useEffect, useRef, useState } from "react";
import type { Zones } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const WS_BASE = import.meta.env.VITE_WS_BASE ?? "";
const TOKEN = import.meta.env.VITE_TOKEN ?? "";

function authHeaders(extra?: HeadersInit): HeadersInit {
  return { ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}), ...(extra || {}) };
}

function wsUrl(): string {
  if (WS_BASE) return `${WS_BASE}/ws${TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : ""}`;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws${TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : ""}`;
}

export async function apiGet<T = unknown>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText || "request failed"}`);
  return r.json();
}

export async function apiPost<T = unknown>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = r.statusText || "request failed";
    try {
      const payload = await r.json();
      detail = String(payload?.detail || payload?.error || detail);
    } catch {
      /* keep HTTP status text */
    }
    throw new Error(`${r.status} ${detail}`.trim());
  }
  return r.json();
}

export interface ChatEvent {
  type: "context" | "token" | "done" | "error";
  text?: string;
  detail?: string;
  rag_titles?: string[];
  runs?: number;
}

/** Stream /api/chat (SSE over POST). Calls onEvent per parsed event. */
export async function streamChat(query: string, onEvent: (e: ChatEvent) => void, signal?: AbortSignal): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ query }),
    signal,
  });
  if (!resp.ok) {
    onEvent({ type: "error", detail: `HTTP ${resp.status} ${resp.statusText || ""}`.trim() });
    return;
  }
  if (!resp.body) {
    onEvent({ type: "error", detail: "no response body" });
    return;
  }
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const emit = (part: string) => {
    const line = part.trim();
    if (!line.startsWith("data:")) return;
    try {
      onEvent(JSON.parse(line.slice(5).trim()) as ChatEvent);
    } catch {
      /* skip */
    }
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) emit(part);
  }
  // Flush a trailing frame that lacked a closing blank line.
  if (buf.trim()) emit(buf);
}

export interface CockpitState {
  zones: Zones;
  connected: boolean;
  lastUpdate: number | null;
}

export function useCockpit(): CockpitState {
  const [zones, setZones] = useState<Zones>({});
  const [connected, setConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const pollRef = useRef<number | null>(null);
  const backoff = useRef(1000);

  useEffect(() => {
    let closed = false;

    async function poll() {
      try {
        const all = await apiGet<Zones>("/api/all");
        setZones(all);
        setLastUpdate(Date.now());
      } catch {
        /* WS retry recovers */
      }
    }
    function startPolling() {
      if (pollRef.current == null) { poll(); pollRef.current = window.setInterval(poll, 10000); }
    }
    function stopPolling() {
      if (pollRef.current != null) { window.clearInterval(pollRef.current); pollRef.current = null; }
    }

    function connect() {
      if (closed) return;
      let ws: WebSocket;
      try { ws = new WebSocket(wsUrl()); }
      catch { startPolling(); schedule(); return; }
      ws.onopen = () => { setConnected(true); backoff.current = 1000; stopPolling(); };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg && msg.zone) { setZones((p) => ({ ...p, [msg.zone]: msg.payload })); setLastUpdate(Date.now()); }
        } catch { /* ignore */ }
      };
      ws.onclose = () => { setConnected(false); startPolling(); schedule(); };
      ws.onerror = () => ws.close();
    }
    function schedule() {
      if (closed) return;
      const d = Math.min(backoff.current, 15000); backoff.current = d * 2;
      window.setTimeout(connect, d);
    }

    connect();
    return () => { closed = true; stopPolling(); };
  }, []);

  return { zones, connected, lastUpdate };
}
