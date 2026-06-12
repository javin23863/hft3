import type { PortfolioZone } from "../types";
import { Sparkline } from "./Sparkline";
import { StatusDot } from "./StatusDot";

function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v >= 0 ? "" : "-"}$${Math.abs(v).toFixed(2)}`;
}

export function PortfolioPanel({ z }: { z?: PortfolioZone }) {
  if (!z) return <div className="panel"><h2>Portfolio</h2><div className="muted">loading…</div></div>;
  const pnls = z.fills.recent
    .map((r) => Number(r.pnl))
    .filter((n) => Number.isFinite(n));
  const net = z.pnl.net_pnl;
  return (
    <div className="panel">
      <h2>
        <StatusDot status={z.health} /> Portfolio
        <span className="right muted">{z.live_session ? "LIVE" : z.execution_mode}</span>
      </h2>
      {z.banner && <div className="banner">{z.banner}</div>}
      <div className="metrics" style={{ marginBottom: 10 }}>
        <div className="metric"><div className="k">Net PnL</div><div className={`v ${net != null && net < 0 ? "neg" : "pos"}`}>{money(net)}</div></div>
        <div className="metric"><div className="k">Realized</div><div className="v">{money(z.pnl.realized)}</div></div>
        <div className="metric"><div className="k">Unrealized</div><div className="v">{money(z.pnl.unrealized)}</div></div>
        <div className="metric"><div className="k">ES (5%)</div><div className="v">{z.pnl.expected_shortfall_5pct != null ? z.pnl.expected_shortfall_5pct.toFixed(2) : "—"}</div></div>
      </div>
      <div className="card" style={{ marginBottom: 10 }}>
        <h3>Cumulative PnL (recent fills)</h3>
        <Sparkline values={pnls} />
      </div>
      {z.adverse_selection_ticks && (
        <div className="card" style={{ marginBottom: 10 }}>
          <h3>Adverse selection (ticks)</h3>
          <div className="kv">
            {Object.entries(z.adverse_selection_ticks).map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}>
                <div className="k">{k}</div><div className="v">{v.toFixed(3)}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="muted" style={{ fontSize: 11 }}>
        positions: {z.positions.length} · fills: {z.fills.total} · src: {z.live_session ? "rithmic_pnl" : "replay"}
      </div>
    </div>
  );
}
