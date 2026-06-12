import { useZones } from "../zonesContext";
import { Panel, Dot } from "../ui";
import type { LifecycleZone } from "../types";

const LANES = ["CANDIDATE", "SCREENING", "GAUNTLET", "CERTIFIED", "SHADOW", "LIVE", "DEGRADED", "ARCHIVED_PAUSED", "QUARANTINED", "RETIRED"];

export function LifecycleView() {
  const z = useZones().lifecycle as LifecycleZone | undefined;
  if (!z) return <Panel title="Model Lifecycle"><span className="text-ink-dim">loading…</span></Panel>;
  if (!z.registered) return <Panel title="Model Lifecycle"><div className="rounded-lg border border-line p-3 text-ink-dim">{z.note}</div></Panel>;
  return (
    <div className="space-y-5">
      <Panel title="Lifecycle funnel" right={`${z.total_models} models · ${z.live} live`}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {LANES.map((s) => (
            <div key={s} className="rounded-lg border border-line bg-bg-elev/50 p-2 text-center">
              <div className="text-[10px] uppercase tracking-wide text-ink-faint">{s}</div>
              <div className="kpi-v text-lg">{z.funnel[s] ?? 0}</div>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Models">
        <div className="scroll-area max-h-[62vh] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-bg-panel"><tr><th className="th">model</th><th className="th">hyp</th><th className="th">sym</th><th className="th">state</th><th className="th">route</th><th className="th">demotion</th></tr></thead>
            <tbody>
              {z.rows.map((r) => (
                <tr key={r.id}>
                  <td className="td">{r.id}</td><td className="td">{r.hypothesis_id ?? "—"}</td><td className="td">{r.symbol ?? "—"}</td>
                  <td className="td"><span className="inline-flex items-center gap-1.5"><Dot status={r.dot} />{r.state}</span></td>
                  <td className="td text-ink-dim">{r.route ?? "—"}</td><td className="td text-ink-dim">{r.demotion_reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
