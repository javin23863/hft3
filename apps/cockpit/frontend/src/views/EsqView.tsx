import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { useZones } from "../zonesContext";
import { Badge, Dot, Kpi, Panel, money } from "../ui";
import type { EsqZone } from "../types";

const AX = { stroke: "#6b7689", fontSize: 11 };

function fmtAge(min: number | null): string {
  if (min == null) return "—";
  if (min < 60) return `${min.toFixed(0)}m`;
  const h = min / 60;
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

function ageTone(min: number | null): "ok" | "bad" | "warn" | "dim" {
  if (min == null) return "dim";
  if (min > 120) return "bad";
  if (min > 60) return "warn";
  return "ok";
}

function verdictTone(v?: string | null): "ok" | "bad" | "warn" | "dim" {
  if (v === "PASS") return "ok";
  if (v === "WARN") return "warn";
  if (v === "FAIL") return "bad";
  return "dim";
}

export function EsqView() {
  const z = useZones().esq as EsqZone | undefined;
  if (!z) return <Panel title="ESQ Futures"><span className="text-ink-dim">loading…</span></Panel>;
  if (z.health === "gray") {
    return (
      <Panel title="ESQ Futures">
        <div className="rounded-lg border border-line p-3 text-ink-dim">esq repo not found / no data yet.</div>
      </Panel>
    );
  }

  const hb = z.heartbeat;
  const audit = z.audit;
  const bands = audit?.bands;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Kpi label="Last bar" value={hb?.last_bar ?? "—"} />
        <Kpi label="Close" value={hb?.close != null ? hb.close.toFixed(2) : "—"} />
        <Kpi label="Action" value={hb?.action ?? "—"} />
        <Kpi label="Heartbeat age" value={fmtAge(z.heartbeat_age_min)} tone={ageTone(z.heartbeat_age_min)} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-ink-faint">audit verdict</span>
        <Badge tone={verdictTone(audit?.verdict)}>{audit?.verdict ?? "—"}</Badge>
        {audit?.dormant && <Badge tone="warn">dormant</Badge>}
        <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-ink-dim">
          <Dot status={z.health} /> {z.health}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Kpi label="Net PnL" value={money(z.stats.net_usd)} tone={(z.stats.net_usd ?? 0) < 0 ? "bad" : "ok"} />
        <Kpi label="Win rate" value={z.stats.win_rate != null ? `${(z.stats.win_rate * 100).toFixed(1)}%` : "—"} />
        <Kpi label="Profit factor" value={z.stats.profit_factor != null ? z.stats.profit_factor.toFixed(2) : "—"} />
        <Kpi label="Max DD" value={money(z.stats.max_dd_usd)} tone="warn" />
      </div>

      <Panel title="Shadow equity curve" right={`${z.stats.n_trades} trades`}>
        {z.equity_curve.length ? (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={z.equity_curve}>
              <XAxis dataKey="ts" {...AX} tick={false} />
              <YAxis {...AX} width={56} />
              <Tooltip contentStyle={{ background: "#141926", border: "1px solid #222a3a", borderRadius: 8 }} />
              <Line type="monotone" dataKey="equity" stroke="#5b8cff" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : <div className="text-ink-faint">no shadow trades yet</div>}
      </Panel>

      {bands && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Kpi label="net p5" value={money(bands.net_p5)} />
          <Kpi label="net p50" value={money(bands.net_p50)} />
          <Kpi label="net p95" value={money(bands.net_p95)} />
          <Kpi label="dd p99" value={money(bands.dd_p99)} />
        </div>
      )}

      {z.sizing.trades_per_year != null && (
        <div className="text-xs text-ink-faint">trades/year (sizing memo): {z.sizing.trades_per_year}</div>
      )}

      <Panel title="Recent shadow trades" right="last 20 · newest first">
        <div className="scroll-area max-h-[50vh] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-bg-panel">
              <tr>
                <th className="th">entry_ts</th>
                <th className="th">exit_reason</th>
                <th className="th">ev</th>
                <th className="th">pnl_usd</th>
              </tr>
            </thead>
            <tbody>
              {z.recent_trades.map((t, i) => (
                <tr key={`${t.entry_ts ?? "row"}-${i}`}>
                  <td className="td mono text-xs">{t.entry_ts ?? "—"}</td>
                  <td className="td text-ink-dim">{t.exit_reason ?? "—"}</td>
                  <td className="td text-right mono">{t.ev != null ? Number(t.ev).toFixed(3) : "—"}</td>
                  <td className={`td text-right mono ${(t.pnl_usd ?? 0) >= 0 ? "text-ok" : "text-bad"}`}>
                    {money(t.pnl_usd as number | null)}
                  </td>
                </tr>
              ))}
              {!z.recent_trades.length && (
                <tr><td className="td text-ink-faint" colSpan={4}>no trades yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
