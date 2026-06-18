import { useState } from "react";
import { useZones } from "../zonesContext";
import { Panel, Badge } from "../ui";
import { ModelDetail } from "../components/ModelDetail";
import type { ModelsZone, ModelRow } from "../types";

type Filter = "all" | "prop" | "dead" | "traded";

export function ModelsView() {
  const z = useZones().models as ModelsZone | undefined;
  const [filter, setFilter] = useState<Filter>("all");
  const [openId, setOpenId] = useState<number | null>(null);

  if (!z) return <Panel title="Models"><span className="text-ink-dim">loading…</span></Panel>;
  const rows = z.rows.filter((r) =>
    filter === "all" ? true : filter === "prop" ? r.family === "prop" : filter === "dead" ? r.structurally_dead : r.total_trades > 0);
  const fn = z.funnel;
  const vix = z.vix_coverage;

  const statusTone = (r: ModelRow): "ok" | "bad" | "warn" | "dim" =>
    r.structurally_dead ? "bad" : r.status === "positive" ? "ok" : r.status === "negative" ? "warn" : "dim";

  return (
    <div className="space-y-5">
      <Panel title="Model tracker" right={`${fn.registry} hyp · ${fn.slug_registry_total ?? "—"} slugs · ${fn.screened_stage_a} screened · ${fn.survivors_stage_a} survivors · ${fn.structurally_dead} dead`}>
        {z.silent_zero.count > 0 && (
          <div className="mb-3 rounded-lg border border-bad/40 bg-bad/5 p-2 text-sm text-ink-dim">
            ⚠ silent-zero: {z.silent_zero.count} hyps never alive ({z.silent_zero.hypotheses.map((h) => `#${h.id}`).join(", ")}). Not tested-and-rejected.
          </div>
        )}
        {vix && (
          <div className="mb-3 rounded-lg border border-border/70 bg-bg-elev/40 p-2 text-sm text-ink-dim">
            <span className="font-medium text-ink">VIX coverage:</span>{" "}
            <span className="mono">{vix.cell_event_observations_with_vix}/{vix.cell_event_observations}</span>{" "}
            cell-event observations
            {vix.coverage_pct != null ? <span className="mono"> ({vix.coverage_pct.toFixed(2)}%)</span> : null}{" "}
            <span className="ml-2 text-ink-faint">{vix.status}</span>
            {vix.invalid_cells ? <span className="ml-2 text-bad">invalid cells: {vix.invalid_cells}</span> : null}
          </div>
        )}
        <div className="mb-3 flex gap-2">
          {(["all", "prop", "dead", "traded"] as Filter[]).map((f) => (
            <button key={f} onClick={() => setFilter(f)}
              className={`chip ${filter === f ? "border-accent/60 text-accent" : ""}`}>{f}</button>
          ))}
          <span className="ml-auto self-center text-[11px] text-ink-faint">click a row → construction + results</span>
        </div>
        <div className="scroll-area max-h-[62vh] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-bg-panel">
              <tr><th className="th">#</th><th className="th">hypothesis</th><th className="th">family</th><th className="th">status</th>
                <th className="th text-right">trades</th><th className="th text-right">evt types</th><th className="th text-right">VIX obs</th><th className="th text-right">mean E[$]</th><th className="th text-right">worst tail</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} onClick={() => setOpenId(r.id)}
                  className="cursor-pointer hover:bg-bg-elev/60">
                  <td className="td">{r.id}</td>
                  <td className="td">{r.name}</td>
                  <td className="td"><span className="text-ink-faint">{r.family}</span></td>
                  <td className="td"><Badge tone={statusTone(r)}>{r.structurally_dead ? "dead" : r.status}</Badge></td>
                  <td className="td text-right mono">{r.total_trades}</td>
                  <td className="td text-right mono">{r.n_event_types}</td>
                  <td className="td text-right mono">
                    {r.n_events != null && r.n_events > 0 ? `${r.n_events_with_vix ?? 0}/${r.n_events}` : "—"}
                  </td>
                  <td className={`td text-right mono ${r.mean_expectancy_usd != null ? (r.mean_expectancy_usd >= 0 ? "text-ok" : "text-bad") : ""}`}>
                    {r.mean_expectancy_usd != null ? r.mean_expectancy_usd.toFixed(2) : "—"}</td>
                  <td className="td text-right mono text-bad">{r.worst_event_tail_usd != null ? Number(r.worst_event_tail_usd).toFixed(2) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <ModelDetail hypId={openId} open={openId != null} onClose={() => setOpenId(null)} />
    </div>
  );
}
