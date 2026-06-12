import { useState } from "react";
import type { ModelsZone, ModelRow } from "../types";
import { StatusDot } from "./StatusDot";

type Filter = "all" | "prop" | "dead" | "traded";

function rowVisible(r: ModelRow, f: Filter): boolean {
  if (f === "all") return true;
  if (f === "prop") return r.family === "prop";
  if (f === "dead") return r.structurally_dead;
  if (f === "traded") return r.total_trades > 0;
  return true;
}

export function ModelGrid({ z }: { z?: ModelsZone }) {
  const [filter, setFilter] = useState<Filter>("all");
  if (!z) return <div className="panel"><h2>Models</h2><div className="muted">loading…</div></div>;
  const rows = z.rows.filter((r) => rowVisible(r, filter));
  const fn = z.funnel;
  return (
    <div className="panel">
      <h2>
        <StatusDot status={z.health} /> Model Tracker
        <span className="right muted">
          {fn.registry} reg · {fn.screened_stage_a} screened · {fn.survivors_stage_a} survivors · {fn.structurally_dead} dead
        </span>
      </h2>

      {z.silent_zero.count > 0 && (
        <div className="banner" style={{ borderLeftColor: "var(--red)" }}>
          ⚠ silent-zero: {z.silent_zero.count} hyps never alive (no producer / no context / hardcoded 0) —{" "}
          {z.silent_zero.hypotheses.map((h) => `#${h.id}`).join(", ")}. Not tested-and-rejected.
        </div>
      )}

      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        {(["all", "prop", "dead", "traded"] as Filter[]).map((f) => (
          <button key={f} className="badge" style={{ cursor: "pointer", background: filter === f ? "var(--panel2)" : "transparent", color: filter === f ? "var(--fg)" : undefined }} onClick={() => setFilter(f)}>{f}</button>
        ))}
      </div>

      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>#</th><th>hypothesis</th><th>family</th><th>status</th>
              <th className="num">trades</th><th className="num">evt types</th><th className="num">mean E[$]</th><th className="num">worst tail</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className={r.structurally_dead ? "dead" : ""}>
                <td>{r.id}</td>
                <td>{r.name}</td>
                <td className="fam">{r.family}</td>
                <td><span className={`badge ${r.structurally_dead ? "dead" : r.status}`}>{r.structurally_dead ? "dead" : r.status}</span></td>
                <td className="num">{r.total_trades}</td>
                <td className="num">{r.n_event_types}</td>
                <td className={`num ${r.mean_expectancy_usd != null ? (r.mean_expectancy_usd >= 0 ? "pos" : "neg") : ""}`}>
                  {r.mean_expectancy_usd != null ? r.mean_expectancy_usd.toFixed(2) : "—"}
                </td>
                <td className="num neg">{r.worst_event_tail_usd != null ? Number(r.worst_event_tail_usd).toFixed(2) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
