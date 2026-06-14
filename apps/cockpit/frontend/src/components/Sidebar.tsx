import { NavLink } from "react-router-dom";
import {
  LayoutDashboard, GitBranch, Boxes, CircleDollarSign, Activity, ShieldAlert, Cpu, Wallet, MessageSquare,
} from "lucide-react";
import { useZones } from "../zonesContext";
import { Dot } from "../ui";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, zone: null },
  { to: "/pipeline", label: "Pipeline", icon: GitBranch, zone: "pipeline" },
  { to: "/models", label: "Models", icon: Boxes, zone: "models" },
  { to: "/options", label: "Options", icon: CircleDollarSign, zone: "options" },
  { to: "/lifecycle", label: "Lifecycle", icon: Activity, zone: "lifecycle" },
  { to: "/autonomy", label: "Autonomy", icon: ShieldAlert, zone: "autonomy" },
  { to: "/system", label: "System", icon: Cpu, zone: "system" },
  { to: "/portfolio", label: "Portfolio", icon: Wallet, zone: "portfolio" },
  { to: "/chat", label: "Assistant", icon: MessageSquare, zone: null },
] as const;

export function Sidebar() {
  const zones = useZones() as Record<string, { health?: string }>;
  return (
    <aside className="flex w-56 flex-none flex-col border-r border-line bg-bg-soft/70 backdrop-blur">
      <div className="flex items-center gap-2 px-4 py-4">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent/20 text-accent font-mono text-sm font-bold">H3</div>
        <div>
          <div className="text-sm font-semibold leading-tight">HFT3 Cockpit</div>
          <div className="text-[10px] uppercase tracking-widest text-ink-faint">CME microstructure</div>
        </div>
      </div>
      <nav className="flex flex-col gap-0.5 px-2 py-2">
        {NAV.map(({ to, label, icon: Icon, zone }) => (
          <NavLink key={to} to={to} end={to === "/"}
            className={({ isActive }) => `navlink ${isActive ? "navlink-active" : ""}`}>
            <Icon size={16} className="flex-none opacity-80" />
            <span className="flex-1">{label}</span>
            {zone && <Dot status={zones[zone]?.health} glow={false} />}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto px-4 py-3 text-[10px] leading-relaxed text-ink-faint">
        read-only · detect-only doctrine · control plane local-origin only
      </div>
    </aside>
  );
}
