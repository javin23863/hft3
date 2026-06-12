import { useZones } from "../zonesContext";
import { Panel, Kpi } from "../ui";
import type { AutonomyZone } from "../types";

export function AutonomyView() {
  const z = useZones().autonomy as AutonomyZone | undefined;
  if (!z) return <Panel title="Autonomy"><span className="text-ink-dim">loading…</span></Panel>;
  if (!z.available) return <Panel title="Autonomy"><div className="rounded-lg border border-line p-3 text-ink-dim">unavailable: {z.error}</div></Panel>;
  const br = z.breaker;
  const jobs = z.jobs ?? {};
  return (
    <div className="space-y-5">
      {br?.frozen && (
        <div className="panel border-bad/50 p-3 text-bad">⛔ circuit breaker FROZEN — {br.reason || "tripped"}. Human unfreeze required.</div>
      )}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <Kpi label="master enable" value={z.master_enabled ? "ON" : "off"} tone={z.master_enabled ? "bad" : "dim"} />
        <Kpi label="arm to live" value={z.can_arm_live ? "ON" : "off"} tone={z.can_arm_live ? "bad" : "dim"} />
        <Kpi label="kill engaged" value={z.kill_engaged ? "YES" : "no"} tone={z.kill_engaged ? "warn" : "dim"} />
        <Kpi label="breaker" value={br?.frozen ? "FROZEN" : "closed"} tone={br?.frozen ? "bad" : "ok"} />
        <Kpi label="audit chain" value={z.audit_chain_ok ? "ok" : "BROKEN"} tone={z.audit_chain_ok ? "ok" : "bad"} />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Jobs">
          <div className="grid grid-cols-4 gap-2 text-center">
            {["pending", "running", "done", "failed"].map((k) => (
              <div key={k} className="rounded-lg border border-line bg-bg-elev/50 p-2">
                <div className="text-[10px] uppercase text-ink-faint">{k}</div><div className="kpi-v text-lg">{jobs[k] ?? 0}</div>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Recent decisions">
          <div className="scroll-area max-h-64 overflow-auto">
            <table className="w-full text-sm">
              <thead><tr><th className="th">event</th><th className="th">model</th><th className="th">ts</th></tr></thead>
              <tbody>
                {(z.recent_audit ?? []).slice(-15).reverse().map((r, i) => (
                  <tr key={i}>
                    <td className="td">{String((r as Record<string, unknown>).event_type ?? "")}</td>
                    <td className="td text-ink-dim">{String((r as Record<string, unknown>).model_id ?? "—")}</td>
                    <td className="td text-ink-faint mono">{String((r as Record<string, unknown>).ts ?? "").replace("T", " ").slice(0, 19)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
    </div>
  );
}
