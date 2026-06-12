import { Bell, Wifi, WifiOff } from "lucide-react";
import { useZones } from "../zonesContext";
import { Dot, Badge } from "../ui";
import type { SystemZone, AlertsZone } from "../types";

export function TopBar({ connected, lastUpdate }: { connected: boolean; lastUpdate: number | null }) {
  const zones = useZones();
  const mode = (zones.system as SystemZone | undefined)?.execution?.execution_mode as string | undefined;
  const alerts = zones.alerts as AlertsZone | undefined;
  const alertCount = alerts?.count ?? 0;
  const crit = (alerts?.alerts ?? []).some((a) => a.severity === "crit");
  return (
    <header className="flex items-center gap-3 border-b border-line bg-bg-soft/60 px-5 py-3 backdrop-blur">
      <div className="text-sm font-semibold tracking-tight text-ink-dim">Trading Cockpit</div>
      {mode && <Badge tone={mode === "LIVE" ? "bad" : "dim"}>{mode}</Badge>}
      <div className="ml-auto flex items-center gap-4">
        <div className="flex items-center gap-1.5 text-xs text-ink-dim">
          <Bell size={15} className={crit ? "text-bad" : alertCount ? "text-warn" : "text-ink-faint"} />
          <span className="mono">{alertCount}</span>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-ink-dim">
          {connected ? <Wifi size={15} className="text-ok" /> : <WifiOff size={15} className="text-bad" />}
          <span>{connected ? "live" : "reconnecting"}</span>
          <Dot status={connected ? "ok" : "fail"} />
        </div>
        {lastUpdate && <span className="mono text-[11px] text-ink-faint">{new Date(lastUpdate).toLocaleTimeString()}</span>}
      </div>
    </header>
  );
}
