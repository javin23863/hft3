import type { ReactNode } from "react";
import type { SystemZone } from "../types";
import { StatusDot } from "./StatusDot";

function g(obj: Record<string, unknown> | undefined, key: string): unknown {
  return obj ? obj[key] : undefined;
}
function s(v: unknown): string {
  if (v === null || v === undefined) return "—";
  return String(v);
}

function Card({ title, status, children }: { title: string; status?: string; children: ReactNode }) {
  return (
    <div className="card">
      <h3>{status && <StatusDot status={status} />} {title}</h3>
      <div className="kv">{children}</div>
    </div>
  );
}
function Row({ k, v }: { k: string; v: unknown }) {
  return (<><div className="k">{k}</div><div className="v">{s(v)}</div></>);
}

export function SystemPanel({ z }: { z?: SystemZone }) {
  if (!z) return <div className="panel"><h2>System</h2><div className="muted">loading…</div></div>;
  const lat = z.latency, cert = z.certification, slow = z.slow_tier, db = z.databento, cap = z.capture, ex = z.execution;
  const remaining = g(db, "remaining");
  return (
    <div className="panel">
      <h2><StatusDot status={z.health} /> System</h2>
      <div className="sys-cards">
        <Card title="Latency" status={String(g(lat, "status") ?? "unknown")}>
          <Row k="order-ack p99" v={g(lat, "order_ack_p99_ms") != null ? `${Number(g(lat, "order_ack_p99_ms")).toFixed(2)} ms` : "—"} />
          <Row k="lane" v={`${s(g(lat, "recommended_lane"))} ${s(g(lat, "lane_name"))}`} />
          <Row k="verdict" v={g(lat, "overall")} />
          <Row k="bottleneck" v={g(lat, "dominant_bottleneck")} />
        </Card>

        <Card title="Certification" status={String(g(cert, "status") ?? "unknown")}>
          <Row k="status" v={g(cert, "certification_status")} />
          <Row k="run" v={String(g(cert, "run_id") ?? "—").slice(0, 28)} />
          <Row k="blocking" v={(g(cert, "blocking_failures") as unknown[] | undefined)?.length ?? 0} />
          <Row k="exec modes" v={(g(cert, "execution_modes") as string[] | undefined)?.join(",")} />
        </Card>

        <Card title="Slow-tier LLM" status={String(g(slow, "status") ?? "unknown")}>
          <Row k="problems" v={g(slow, "n_problems")} />
          <Row k="eval agree" v={(() => { const e = g(slow, "eval") as Record<string, unknown> | undefined; const a = e?.agreement; return a != null ? `${(Number(a) * 100).toFixed(0)}%` : "—"; })()} />
          <Row k="gate" v={(() => { const e = g(slow, "eval") as Record<string, unknown> | undefined; return e?.gate_pass; })()} />
          <Row k="age" v={g(slow, "problems_age")} />
        </Card>

        <Card title="Databento budget">
          <Row k="used" v={g(db, "total_used") != null ? `$${Number(g(db, "total_used")).toFixed(2)}` : "—"} />
          <Row k="remaining" v={remaining != null ? `$${Number(remaining).toFixed(2)}` : "—"} />
          <Row k="cap" v={`$${s(g(db, "operating_cap"))}`} />
          <Row k="manifest rows" v={g(db, "manifest_rows")} />
        </Card>

        <Card title="Capture" status={String(g(cap, "status") ?? "unknown")}>
          <Row k="host" v={g(cap, "host")} />
          <Row k="captured" v={String(g(cap, "captured_at") ?? "—").replace("T", " ").slice(0, 19)} />
          <Row k="known gaps" v={(g(cap, "known_gaps") as unknown[] | undefined)?.length ?? 0} />
          <Row k="cpu" v={(() => { const c = g(cap, "cpu_layout") as Record<string, unknown> | undefined; return c ? `hot ${c.hot_cpus}` : "—"; })()} />
        </Card>

        <Card title="Execution" status={String(g(ex, "live")) === "true" ? "fail" : "ok"}>
          <Row k="mode" v={g(ex, "execution_mode")} />
          <Row k="kill switch" v={g(ex, "kill_switch")} />
          <Row k="live" v={String(g(ex, "live"))} />
        </Card>
      </div>
    </div>
  );
}
