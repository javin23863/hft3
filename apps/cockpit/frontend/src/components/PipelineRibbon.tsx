import type { PipelineZone, Stage } from "../types";
import { StatusDot } from "./StatusDot";

function stageMeta(s: Stage): string[] {
  const out: string[] = [];
  const push = (label: string, key: string) => {
    const v = s[key];
    if (v !== undefined && v !== null && v !== "") out.push(`${label}: ${v}`);
  };
  push("run", "units_run");
  push("skip", "units_skipped");
  push("err", "units_errored");
  push("survivors", "survivors");
  push("rows", "row_count");
  push("gaps", "known_gaps");
  push("cands", "candidates");
  const ts = (s.run_end || s.captured_at || s.generated_at) as string | undefined;
  if (ts) out.push(String(ts).replace("T", " ").slice(0, 19));
  return out.slice(0, 4);
}

export function PipelineRibbon({ z }: { z?: PipelineZone }) {
  if (!z) return <div className="panel"><h2>Pipeline</h2><div className="muted">loading…</div></div>;
  return (
    <div className="panel">
      <h2><StatusDot status={z.health} /> Pipeline <span className="right muted">start → end</span></h2>
      <div className="ribbon">
        {z.stages.map((s, i) => (
          <div className="stage" key={s.id} style={{ display: "flex", gap: 8 }}>
            <div style={{ flex: 1 }}>
              <div className="name"><StatusDot status={s.status} /> {s.label}</div>
              <div className="meta">{stageMeta(s).map((m, j) => <div key={j}>{m}</div>)}</div>
            </div>
            {i < z.stages.length - 1 && <div className="arrow">▸</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
