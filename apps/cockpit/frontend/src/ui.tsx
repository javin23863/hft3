import type { ReactNode } from "react";

const HEALTH: Record<string, string> = {
  ok: "bg-ok", green: "bg-ok", running: "bg-run", stale: "bg-warn", amber: "bg-warn",
  unknown: "bg-warn", fail: "bg-bad", red: "bg-bad", missing: "bg-bad",
};

export function Dot({ status, glow = true }: { status?: string; glow?: boolean }) {
  const c = HEALTH[status ?? "unknown"] ?? "bg-ink-faint";
  return <span className={`inline-block h-2.5 w-2.5 flex-none rounded-full ${c} ${glow ? "ring-2 ring-current/20" : ""}`} title={status} />;
}

export function Panel({ title, right, children, className = "" }: { title?: string; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-h">
          <span>{title}</span>
          {right && <span className="ml-auto font-normal normal-case tracking-normal text-ink-dim">{right}</span>}
        </div>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Kpi({ label, value, tone }: { label: string; value: ReactNode; tone?: "ok" | "bad" | "warn" | "dim" }) {
  const c = tone === "ok" ? "text-ok" : tone === "bad" ? "text-bad" : tone === "warn" ? "text-warn" : "text-ink";
  return (
    <div className="panel p-4">
      <div className="text-[11px] uppercase tracking-wide text-ink-faint">{label}</div>
      <div className={`kpi-v ${c} mt-1`}>{value}</div>
    </div>
  );
}

export function Badge({ children, tone = "dim" }: { children: ReactNode; tone?: "ok" | "bad" | "warn" | "dim" | "accent" }) {
  const map = {
    ok: "border-ok/40 text-ok", bad: "border-bad/40 text-bad", warn: "border-warn/40 text-warn",
    accent: "border-accent/50 text-accent", dim: "border-line text-ink-dim",
  };
  return <span className={`chip ${map[tone]}`}>{children}</span>;
}

export function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(2)}`;
}
