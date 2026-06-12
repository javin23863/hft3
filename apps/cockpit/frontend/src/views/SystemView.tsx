import { useZones } from "../zonesContext";
import { Panel, Dot } from "../ui";
import type { SystemZone } from "../types";

function g(o: Record<string, unknown> | undefined, k: string): unknown {
  return o ? o[k] : undefined;
}
function s(v: unknown): string {
  return v === null || v === undefined ? "—" : String(v);
}

function Card({ title, status, rows }: { title: string; status?: string; rows: [string, unknown][] }) {
  return (
    <div className="panel p-4">
      <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-ink-dim">
        {status && <Dot status={status} />} {title}
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <div className="text-ink-faint">{k}</div>
            <div className="mono text-right text-ink">{s(v)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function SystemView() {
  const sys = useZones().system as SystemZone | undefined;
  if (!sys) return <Panel title="System"><span className="text-ink-dim">loading…</span></Panel>;
  const lat = sys.latency, cert = sys.certification, slow = sys.slow_tier, db = sys.databento, cap = sys.capture, ex = sys.execution;
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Card title="Latency" status={String(g(lat, "status") ?? "unknown")} rows={[
        ["order-ack p99", g(lat, "order_ack_p99_ms") != null ? `${Number(g(lat, "order_ack_p99_ms")).toFixed(2)} ms` : "—"],
        ["lane", `${s(g(lat, "recommended_lane"))} ${s(g(lat, "lane_name"))}`],
        ["verdict", g(lat, "overall")], ["bottleneck", g(lat, "dominant_bottleneck")],
      ]} />
      <Card title="Certification" status={String(g(cert, "status") ?? "unknown")} rows={[
        ["status", g(cert, "certification_status")],
        ["blocking", (g(cert, "blocking_failures") as unknown[] | undefined)?.length ?? 0],
        ["exec modes", (g(cert, "execution_modes") as string[] | undefined)?.join(",")],
      ]} />
      <Card title="Slow-tier LLM" status={String(g(slow, "status") ?? "unknown")} rows={[
        ["problems", g(slow, "n_problems")],
        ["age", g(slow, "problems_age")],
      ]} />
      <Card title="Databento" rows={[
        ["used", g(db, "total_used") != null ? `$${Number(g(db, "total_used")).toFixed(2)}` : "—"],
        ["remaining", g(db, "remaining") != null ? `$${Number(g(db, "remaining")).toFixed(2)}` : "—"],
        ["manifest rows", g(db, "manifest_rows")],
      ]} />
      <Card title="Capture" status={String(g(cap, "status") ?? "unknown")} rows={[
        ["host", g(cap, "host")],
        ["known gaps", (g(cap, "known_gaps") as unknown[] | undefined)?.length ?? 0],
      ]} />
      <Card title="Execution" status={String(g(ex, "live")) === "true" ? "fail" : "ok"} rows={[
        ["mode", g(ex, "execution_mode")], ["kill switch", g(ex, "kill_switch")], ["live", String(g(ex, "live"))],
      ]} />
    </div>
  );
}
