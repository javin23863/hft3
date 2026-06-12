import type { LifecycleZone } from "../types";
import { StatusDot } from "./StatusDot";

const LANES = [
  "CANDIDATE", "SCREENING", "GAUNTLET", "CERTIFIED", "SHADOW", "LIVE",
  "DEGRADED", "ARCHIVED_PAUSED", "QUARANTINED", "RETIRED",
];

export function LifecyclePanel({ z }: { z?: LifecycleZone }) {
  if (!z) return <div className="panel"><h2>Model Lifecycle</h2><div className="muted">loading…</div></div>;
  if (!z.registered) {
    return (
      <div className="panel">
        <h2><StatusDot status={z.health} /> Model Lifecycle</h2>
        <div className="banner">{z.note}</div>
      </div>
    );
  }
  return (
    <div className="panel">
      <h2>
        <StatusDot status={z.health} /> Model Lifecycle
        <span className="right muted">{z.total_models} models · {z.live} live</span>
      </h2>

      <div className="ribbon" style={{ marginBottom: 10 }}>
        {LANES.map((s) => (
          <div className="stage" key={s} style={{ minWidth: 92 }}>
            <div className="name" style={{ fontSize: 11 }}>{s}</div>
            <div className="v" style={{ fontSize: 18, fontWeight: 700 }}>{z.funnel[s] ?? 0}</div>
          </div>
        ))}
      </div>

      <div className="scroll" style={{ maxHeight: 300 }}>
        <table>
          <thead>
            <tr><th>model</th><th>hyp</th><th>sym</th><th>state</th><th>route</th><th>demotion</th><th>since</th></tr>
          </thead>
          <tbody>
            {z.rows.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.hypothesis_id ?? "—"}</td>
                <td>{r.symbol ?? "—"}</td>
                <td><span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><StatusDot status={r.dot} />{r.state}</span></td>
                <td className="fam">{r.route ?? "—"}</td>
                <td className="fam">{r.demotion_reason ?? "—"}</td>
                <td className="muted">{r.since ? String(r.since).replace("T", " ").slice(0, 19) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
