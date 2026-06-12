import { useCockpit } from "./api";
import { AlertCenter } from "./components/AlertCenter";
import { PipelineRibbon } from "./components/PipelineRibbon";
import { PortfolioPanel } from "./components/PortfolioPanel";
import { ModelGrid } from "./components/ModelGrid";
import { SystemPanel } from "./components/SystemPanel";
import { LifecyclePanel } from "./components/LifecyclePanel";
import { AutonomyPanel } from "./components/AutonomyPanel";
import { StatusDot } from "./components/StatusDot";

export default function App() {
  const { zones, connected, lastUpdate } = useCockpit();
  const mode = zones.system?.execution?.execution_mode as string | undefined;
  return (
    <div className="app">
      <div className="topbar">
        <span className="title">HFT3 COCKPIT</span>
        {mode && <span className="badge">{mode}</span>}
        <span className="spacer" />
        <span className="conn">
          <StatusDot status={connected ? "ok" : "fail"} />
          {connected ? "live" : "reconnecting…"}
          {lastUpdate && <span className="muted"> · {new Date(lastUpdate).toLocaleTimeString()}</span>}
        </span>
      </div>

      <AlertCenter z={zones.alerts} />
      <PipelineRibbon z={zones.pipeline} />

      <div className="grid2">
        <PortfolioPanel z={zones.portfolio} />
        <SystemPanel z={zones.system} />
      </div>

      <ModelGrid z={zones.models} />

      <div className="grid2">
        <LifecyclePanel z={zones.lifecycle} />
        <AutonomyPanel z={zones.autonomy} />
      </div>

      <div className="muted" style={{ fontSize: 11, textAlign: "center", padding: "8px 0 20px" }}>
        read-only aggregation · detect-only doctrine · control plane is local-origin only
      </div>
    </div>
  );
}
