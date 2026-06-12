import { useEffect, useRef, useState } from "react";
import type { Zones } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const WS_BASE = import.meta.env.VITE_WS_BASE ?? "";
const TOKEN = import.meta.env.VITE_TOKEN ?? "";

function authHeaders(): HeadersInit {
  return TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {};
}

function wsUrl(): string {
  if (WS_BASE) return `${WS_BASE}/ws${TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : ""}`;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws${TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : ""}`;
}

export interface CockpitState {
  zones: Zones;
  connected: boolean;
  lastUpdate: number | null;
}

/**
 * Live cockpit feed. Opens a WebSocket (first-paint + deltas) and, while the
 * socket is down, falls back to polling /api/all. Reconnects with backoff.
 */
export function useCockpit(): CockpitState {
  const [zones, setZones] = useState<Zones>({});
  const [connected, setConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<number | null>(null);
  const backoff = useRef(1000);

  useEffect(() => {
    let closed = false;

    function applyMessage(zone: string, payload: unknown) {
      setZones((prev) => ({ ...prev, [zone]: payload }));
      setLastUpdate(Date.now());
    }

    async function poll() {
      try {
        const r = await fetch(`${API_BASE}/api/all`, { headers: authHeaders() });
        if (r.ok) {
          const all = await r.json();
          setZones(all);
          setLastUpdate(Date.now());
        }
      } catch {
        /* ignore; WS retry handles recovery */
      }
    }

    function startPolling() {
      if (pollRef.current == null) {
        poll();
        pollRef.current = window.setInterval(poll, 10000);
      }
    }
    function stopPolling() {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }

    function connect() {
      if (closed) return;
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        startPolling();
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        backoff.current = 1000;
        stopPolling();
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg && msg.zone) applyMessage(msg.zone, msg.payload);
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        startPolling();
        scheduleReconnect();
      };
      ws.onerror = () => ws.close();
    }

    function scheduleReconnect() {
      if (closed) return;
      const delay = Math.min(backoff.current, 15000);
      backoff.current = delay * 2;
      window.setTimeout(connect, delay);
    }

    connect();
    return () => {
      closed = true;
      stopPolling();
      wsRef.current?.close();
    };
  }, []);

  return { zones, connected, lastUpdate };
}
