import type { AlertsZone } from "../types";
import { StatusDot } from "./StatusDot";

export function AlertCenter({ z }: { z?: AlertsZone }) {
  const alerts = z?.alerts ?? [];
  if (!z) return null;
  if (alerts.length === 0) {
    return (
      <div className="panel" style={{ marginBottom: 12 }}>
        <div className="nominal"><StatusDot status="green" /> All systems nominal — no active problems.</div>
      </div>
    );
  }
  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <h2><StatusDot status={z.health} /> Alerts <span className="right">{z.count} active</span></h2>
      <div className="alerts-list">
        {alerts.map((a) => (
          <div key={a.id} className={`alert ${a.severity}`}>
            <span className="src">{a.source}</span>
            <span>{a.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
