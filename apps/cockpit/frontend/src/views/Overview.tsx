import { useZones } from "../zonesContext";
import { Kpi, Panel, Dot, Badge } from "../ui";
import type { PipelineZone, ModelsZone, SystemZone, AutonomyZone, AlertsZone } from "../types";

export function Overview() {
  const z = useZones();
  const p = z.pipeline as PipelineZone | undefined;
  const m = z.models as ModelsZone | undefined;
  const s = z.system as SystemZone | undefined;
  const a = z.autonomy as AutonomyZone | undefined;
  const al = z.alerts as AlertsZone | undefined;
  const alertItems = al?.alerts ?? [];
  const hasAlertsPayload = al != null && Array.isArray(al.alerts) && typeof al.count === "number" && typeof al.health === "string";
  const showNominalAlerts = hasAlertsPayload && al.health === "green" && al.count === 0 && alertItems.length === 0;
  const lane = s?.latency?.recommended_lane as number | undefined;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Kpi label="Pipeline" value={<span className="capitalize">{p?.health ?? "—"}</span>}
          tone={p?.health === "green" ? "ok" : p?.health === "red" ? "bad" : "warn"} />
        <Kpi label="Survivors (Stage A)" value={m?.funnel?.survivors_stage_a ?? "—"} />
        <Kpi label="Latency lane" value={lane != null ? `L${lane}` : "—"} tone="dim" />
        <Kpi label="Autonomy" value={a?.master_enabled ? "ON" : "off"} tone={a?.master_enabled ? "bad" : "dim"} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Alerts" right={hasAlertsPayload ? `${al.count} active` : "unknown"}>
          {showNominalAlerts ? (
            <div className="flex items-center gap-2 text-ok"><Dot status="green" /> All systems nominal.</div>
          ) : alertItems.length === 0 ? (
            <div className="flex items-center gap-2 text-ink-dim"><Dot status="unknown" /> {hasAlertsPayload ? "Alert state unavailable." : "Alert state loading."}</div>
          ) : (
            <div className="space-y-2">
              {alertItems.map((x) => (
                <div key={x.id} className={`flex items-start gap-2 rounded-lg border p-2 text-sm ${x.severity === "crit" ? "border-bad/40" : "border-warn/40"}`}>
                  <Badge tone={x.severity === "crit" ? "bad" : "warn"}>{x.source}</Badge>
                  <span className="text-ink-dim">{x.message}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Pipeline" right="start → end">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {(p?.stages ?? []).map((st) => (
              <div key={st.id} className="min-w-0 rounded-lg border border-line bg-bg-elev/50 p-3">
                <div className="flex items-center gap-2 text-sm font-medium"><Dot status={st.status} /> {st.label}</div>
                <div className="mono mt-1 text-[11px] text-ink-faint">{String(st.status)}</div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
