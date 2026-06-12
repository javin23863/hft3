import { Routes, Route } from "react-router-dom";
import { useCockpit } from "./api";
import { ZonesCtx } from "./zonesContext";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { Overview } from "./views/Overview";
import { PipelineView } from "./views/PipelineView";
import { ModelsView } from "./views/ModelsView";
import { LifecycleView } from "./views/LifecycleView";
import { AutonomyView } from "./views/AutonomyView";
import { SystemView } from "./views/SystemView";
import { PortfolioView } from "./views/PortfolioView";
import { ChatView } from "./views/ChatView";

export default function App() {
  const { zones, connected, lastUpdate } = useCockpit();
  return (
    <ZonesCtx.Provider value={zones}>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar connected={connected} lastUpdate={lastUpdate} />
          <main className="scroll-area min-w-0 flex-1 overflow-auto px-5 py-5">
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/pipeline" element={<PipelineView />} />
              <Route path="/models" element={<ModelsView />} />
              <Route path="/lifecycle" element={<LifecycleView />} />
              <Route path="/autonomy" element={<AutonomyView />} />
              <Route path="/system" element={<SystemView />} />
              <Route path="/portfolio" element={<PortfolioView />} />
              <Route path="/chat" element={<ChatView />} />
            </Routes>
          </main>
        </div>
      </div>
    </ZonesCtx.Provider>
  );
}
