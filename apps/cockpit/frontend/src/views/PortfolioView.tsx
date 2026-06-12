import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { useZones } from "../zonesContext";
import { Panel, Kpi, money } from "../ui";
import type { PortfolioZone } from "../types";

const AX = { stroke: "#6b7689", fontSize: 11 };

export function PortfolioView() {
  const z = useZones().portfolio as PortfolioZone | undefined;
  if (!z) return <Panel title="Portfolio"><span className="text-ink-dim">loading…</span></Panel>;
  const pnls = z.fills.recent.map((r) => Number(r.pnl)).filter((n) => Number.isFinite(n));
  let cum = 0;
  const equity = pnls.map((v, i) => ({ i, eq: (cum += v) }));
  const markout = z.adverse_selection_ticks
    ? Object.entries(z.adverse_selection_ticks).map(([k, v]) => ({ h: k.replace("mid_price_plus_", "").replace("ms", "ms"), v }))
    : [];

  return (
    <div className="space-y-5">
      {z.banner && <div className="panel border-warn/40 p-3 text-sm text-ink-dim">{z.banner}</div>}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Kpi label="Net PnL" value={money(z.pnl.net_pnl)} tone={(z.pnl.net_pnl ?? 0) < 0 ? "bad" : "ok"} />
        <Kpi label="Realized" value={money(z.pnl.realized)} />
        <Kpi label="Unrealized" value={money(z.pnl.unrealized)} />
        <Kpi label="ES (5%)" value={z.pnl.expected_shortfall_5pct != null ? z.pnl.expected_shortfall_5pct.toFixed(2) : "—"} />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Cumulative PnL (recent fills)">
          {equity.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={equity}>
                <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#5b8cff" stopOpacity={0.5} /><stop offset="100%" stopColor="#5b8cff" stopOpacity={0} /></linearGradient></defs>
                <XAxis dataKey="i" {...AX} /><YAxis {...AX} width={48} />
                <Tooltip contentStyle={{ background: "#141926", border: "1px solid #222a3a", borderRadius: 8 }} />
                <Area type="monotone" dataKey="eq" stroke="#5b8cff" fill="url(#g)" strokeWidth={1.5} />
              </AreaChart>
            </ResponsiveContainer>
          ) : <div className="text-ink-faint">no fills series</div>}
        </Panel>
        <Panel title="Adverse selection (ticks)">
          {markout.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={markout}>
                <XAxis dataKey="h" {...AX} /><YAxis {...AX} width={48} />
                <Tooltip contentStyle={{ background: "#141926", border: "1px solid #222a3a", borderRadius: 8 }} />
                <Bar dataKey="v" fill="#5b8cff" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <div className="text-ink-faint">no markout data</div>}
        </Panel>
      </div>
      <div className="text-xs text-ink-faint">positions: {z.positions.length} · fills: {z.fills.total} · src: {z.live_session ? "rithmic_pnl" : "replay"}</div>
    </div>
  );
}
