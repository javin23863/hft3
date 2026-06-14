import { useZones } from "../zonesContext";
import { Badge, Dot, Panel } from "../ui";
import type { OptionsZone } from "../types";

function g(o: Record<string, unknown> | undefined, k: string): unknown {
  return o ? o[k] : undefined;
}

function s(v: unknown): string {
  return v === null || v === undefined ? "-" : String(v);
}

function statusTone(status: unknown): "ok" | "bad" | "warn" | "dim" {
  const st = String(status ?? "").toLowerCase();
  return st === "ok" || st === "green" ? "ok" : st === "fail" || st === "red" ? "bad" : "warn";
}

function FieldGrid({ rows }: { rows: [string, unknown][] }) {
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <div className="text-ink-faint">{k}</div>
          <div className="mono text-right text-ink">{s(v)}</div>
        </div>
      ))}
    </div>
  );
}

export function OptionsView() {
  const z = useZones().options as OptionsZone | undefined;
  if (!z) return <Panel title="Options"><span className="text-ink-dim">loading...</span></Panel>;

  const data = z.data_readiness ?? {};
  const defects = z.defect_ledger ?? {};
  const contextCoverage = z.context_feature_coverage ?? {};
  const summary = data["summary"] as Record<string, unknown> | undefined;
  const checks = (data["checks"] as Record<string, unknown>[] | undefined) ?? [];
  const openIds = (defects["open_ids"] as unknown[] | undefined)?.join(", ") || "-";
  const items = (defects["items"] as Record<string, unknown>[] | undefined) ?? [];
  const expiry = summary?.["expiry_coverage"] as Record<string, unknown> | undefined;
  const fixing = summary?.["fixing_mbo"] as Record<string, unknown> | undefined;
  const ohlcv = summary?.["ohlcv"] as Record<string, unknown> | undefined;
  const defs = summary?.["definitions"] as Record<string, unknown> | undefined;
  const stats = summary?.["statistics"] as Record<string, unknown> | undefined;

  return (
    <div className="space-y-4">
      <Panel title="Options Lane" right={<span className="flex items-center gap-2"><Dot status={z.health} />{z.health}</span>}>
        <div className="flex flex-wrap gap-2">
          <Badge tone="accent">{z.lane}</Badge>
          <Badge tone="dim">{z.model_id_prefix}</Badge>
          <Badge tone="warn">{z.phase}</Badge>
          <Badge tone={z.shadow_live_status === "clear" ? "ok" : "bad"}>shadow/live {z.shadow_live_status}</Badge>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <FieldGrid rows={[
            ["research only", String(z.research_only)],
            ["data status", g(data, "status")],
            ["defects", `${s(g(defects, "open_count"))} open`],
            ["blockers", z.shadow_live_blockers.length ? z.shadow_live_blockers.join(", ") : "-"],
            ["context coverage", g(contextCoverage, "status")],
          ]} />
          <FieldGrid rows={[
            ["live controls", String(g(z.controls, "live_order_controls"))],
            ["paper controls", String(g(z.controls, "paper_order_controls"))],
            ["report age", g(data, "report_age")],
            ["lake", g(data, "lake_root")],
          ]} />
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Data Readiness" right={<Badge tone={statusTone(g(data, "status"))}>{s(g(data, "status"))}</Badge>}>
          <FieldGrid rows={[
            ["fixing quote files", g(fixing, "quote_files")],
            ["fixing trades files", g(fixing, "trades_files")],
            ["dates covered", g(fixing, "dates_covered")],
            ["coverage gaps", g(expiry, "gap_count")],
            ["stale gaps", g(expiry, "stale_gap_count")],
            ["ohlcv files", g(ohlcv, "files")],
            ["definition files", g(defs, "files")],
            ["statistics files", g(stats, "files")],
            ["statistics state", g(stats, "state")],
          ]} />
          <div className="mt-4 overflow-auto">
            <table className="w-full text-sm">
              <thead><tr><th className="th">check</th><th className="th">status</th><th className="th">detail</th></tr></thead>
              <tbody>
                {checks.map((c) => (
                  <tr key={String(c["name"] ?? "?")} className="border-t border-line/60">
                    <td className="td mono">{s(c["name"])}</td>
                    <td className="td"><Badge tone={statusTone(c["status"])}>{s(c["status"])}</Badge></td>
                    <td className="td text-ink-dim">{s(c["detail"])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Defect Ledger" right={<Badge tone={statusTone(g(defects, "status"))}>{s(g(defects, "ledger_status"))}</Badge>}>
          <FieldGrid rows={[
            ["open count", g(defects, "open_count")],
            ["open ids", openIds],
            ["artifact", g(defects, "artifact")],
            ["reason", g(defects, "reason")],
          ]} />
          <div className="mt-4 space-y-2">
            {items.slice(0, 9).map((item) => (
              <div key={String(item["id"] ?? item["component"] ?? "?")} className="rounded-lg border border-line bg-bg-elev/40 p-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className="mono text-ink">{s(item["id"])}</span>
                  <span className="text-ink-dim">{s(item["component"])}</span>
                  <Badge tone="bad">{s(item["status"])}</Badge>
                </div>
                <div className="mt-1 text-ink-dim">{s(item["description"])}</div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel title="Context Feature Coverage" right={<Badge tone="warn">{s(g(contextCoverage, "status"))}</Badge>}>
        <FieldGrid rows={[
          ["options as clue", g(contextCoverage, "options_as_clue")],
          ["standalone options", g(contextCoverage, "options_standalone_strategy")],
          ["note", g(contextCoverage, "note")],
        ]} />
      </Panel>
    </div>
  );
}
