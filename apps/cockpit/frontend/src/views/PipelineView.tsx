import { useZones } from "../zonesContext";
import { Panel, Dot } from "../ui";
import type { PipelineZone, Stage } from "../types";

function meta(s: Stage): [string, unknown][] {
  const keys = ["units_run", "units_skipped", "units_errored", "survivors", "row_count", "known_gaps", "candidates", "run_end", "captured_at"];
  return keys.map((k) => [k, s[k]]).filter(([, v]) => v !== undefined && v !== null && v !== "") as [string, unknown][];
}

export function PipelineView() {
  const p = useZones().pipeline as PipelineZone | undefined;
  if (!p) return <Panel title="Pipeline"><span className="text-ink-dim">loading…</span></Panel>;
  return (
    <Panel title="Pipeline — start → end" right={<span className="capitalize">{p.health}</span>}>
      <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-6">
        {p.stages.map((s) => (
          <div key={s.id} className="rounded-lg border border-line bg-bg-elev/50 p-3">
            <div className="flex items-center gap-2 text-sm font-semibold"><Dot status={s.status} /> {s.label}</div>
            <div className="mono mt-2 space-y-0.5 text-[11px] text-ink-faint">
              {meta(s).slice(0, 5).map(([k, v]) => (
                <div key={k}>{k}: {String(v).replace("T", " ").slice(0, 19)}</div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
