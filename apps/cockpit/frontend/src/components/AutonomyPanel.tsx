import type { AutonomyZone } from "../types";
import { StatusDot } from "./StatusDot";

function Flag({ label, on, danger }: { label: string; on?: boolean; danger?: boolean }) {
  const color = on ? (danger ? "var(--red)" : "var(--green)") : "var(--muted)";
  return (
    <div className="metric">
      <div className="k">{label}</div>
      <div className="v" style={{ color, fontSize: 14 }}>{on ? "ON" : "off"}</div>
    </div>
  );
}

export function AutonomyPanel({ z }: { z?: AutonomyZone }) {
  if (!z) return <div className="panel"><h2>Autonomy</h2><div className="muted">loading…</div></div>;
  if (!z.available) {
    return <div className="panel"><h2><StatusDot status="amber" /> Autonomy</h2><div className="banner">unavailable: {z.error}</div></div>;
  }
  const br = z.breaker;
  const jobs = z.jobs ?? {};
  return (
    <div className="panel">
      <h2>
        <StatusDot status={z.health} /> Autonomy
        <span className="right muted">{z.master_enabled ? "ENABLED" : "disabled"}{z.can_arm_live ? " · arm-live ON" : ""}</span>
      </h2>

      {br?.frozen && (
        <div className="banner" style={{ borderLeftColor: "var(--red)" }}>
          ⛔ circuit breaker FROZEN — {br.reason || "tripped"}. Human unfreeze required.
        </div>
      )}

      <div className="metrics" style={{ marginBottom: 10 }}>
        <Flag label="master enable" on={z.master_enabled} danger />
        <Flag label="arm to live" on={z.can_arm_live} danger />
        <Flag label="kill engaged" on={z.kill_engaged} />
        <Flag label="breaker frozen" on={br?.frozen} />
        <Flag label="audit chain" on={z.audit_chain_ok} />
      </div>

      <div className="card" style={{ marginBottom: 10 }}>
        <h3>jobs</h3>
        <div className="kv">
          {["pending", "running", "done", "failed"].map((s) => (
            <div key={s} style={{ display: "contents" }}>
              <div className="k">{s}</div><div className="v">{jobs[s] ?? 0}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>recent decisions</h3>
        <div className="scroll" style={{ maxHeight: 180 }}>
          <table>
            <thead><tr><th>event</th><th>model</th><th>ts</th></tr></thead>
            <tbody>
              {(z.recent_audit ?? []).slice(-12).reverse().map((r, i) => (
                <tr key={i}>
                  <td>{String((r as Record<string, unknown>).event_type ?? "")}</td>
                  <td className="fam">{String((r as Record<string, unknown>).model_id ?? "—")}</td>
                  <td className="muted">{String((r as Record<string, unknown>).ts ?? "").replace("T", " ").slice(0, 19)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
