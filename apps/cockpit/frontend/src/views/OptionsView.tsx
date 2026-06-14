import { useZones } from "../zonesContext";
import { Badge, Dot, Panel } from "../ui";
import type { OptionsZone } from "../types";

function g(o: Record<string, unknown> | undefined, k: string): unknown {
  return o ? o[k] : undefined;
}

function s(v: unknown): string {
  if (Array.isArray(v)) return v.length ? v.join(", ") : "-";
  return v === null || v === undefined ? "-" : String(v);
}

function statusTone(status: unknown): "ok" | "bad" | "warn" | "dim" {
  const st = String(status ?? "").toLowerCase();
  return st === "ok" || st === "green" || st === "allowed" || st === "clear" || st === "pass" || st === "real_data_backed"
    ? "ok"
    : st === "fail" || st === "red" || st === "blocked" || st === "artifact_degraded"
      ? "bad"
      : "warn";
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
  const standalone = z.standalone_model_evidence ?? {};
  const legacyFixture = z.legacy_options_fixture_evidence ?? {};
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
          <Badge tone={statusTone(z.research_backtest_status)}>research/backtest {z.research_backtest_status}</Badge>
          <Badge tone={z.shadow_live_status === "clear" ? "ok" : "bad"}>shadow/live {z.shadow_live_status}</Badge>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <FieldGrid rows={[
            ["research/backtest", z.research_backtest_status],
            ["execution", z.execution_status],
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

      <Panel title="Standalone Options Models" right={<Badge tone={statusTone(g(standalone, "status"))}>{s(g(standalone, "status"))}</Badge>}>
        <div className="grid gap-3 md:grid-cols-2">
          <FieldGrid rows={[
            ["lane", g(standalone, "lane")],
            ["model prefix", g(standalone, "model_id_prefix")],
            ["latest model", g(standalone, "latest_model_id")],
            ["latest lane", g(standalone, "latest_lane")],
            ["latest artifact", g(standalone, "latest_artifact")],
            ["artifact status", g(standalone, "latest_artifact_status")],
            ["artifact time", g(standalone, "latest_artifact_time_utc")],
            ["time source", g(standalone, "latest_artifact_time_source")],
            ["summary status", g(standalone, "latest_summary_status")],
            ["robustness", g(standalone, "robustness_status")],
          ]} />
          <FieldGrid rows={[
            ["real data backed", String(g(standalone, "real_data_backed"))],
            ["claimed real data", String(g(standalone, "claimed_real_data_backed"))],
            ["missing proof", g(standalone, "missing_real_data_proof")],
            ["fixture backed", String(g(standalone, "fixture_backed"))],
            ["structural only", String(g(standalone, "structural_only"))],
            ["degraded", String(g(standalone, "degraded"))],
            ["promotable", String(g(standalone, "promotable"))],
            ["trade count", g(standalone, "trade_count")],
            ["next artifact", g(standalone, "next_required_artifact")],
            ["robustness artifact", g(standalone, "robustness_artifact")],
            ["fixture contract", g(standalone, "fixture_contract_path")],
            ["failure notes", g(standalone, "failure_notes")],
          ]} />
        </div>
        <div className="mt-3 text-sm text-ink-dim">{s(g(standalone, "robustness_detail"))}</div>
      </Panel>

      <Panel title="Legacy Options/Parity Fixture" right={<Badge tone={statusTone(g(legacyFixture, "status"))}>{s(g(legacyFixture, "status"))}</Badge>}>
        <div className="grid gap-3 md:grid-cols-2">
          <FieldGrid rows={[
            ["lane", g(legacyFixture, "lane")],
            ["model prefix", g(legacyFixture, "model_id_prefix")],
            ["latest model", g(legacyFixture, "latest_model_id")],
            ["latest lane", g(legacyFixture, "latest_lane")],
            ["latest artifact", g(legacyFixture, "latest_artifact")],
            ["artifact status", g(legacyFixture, "latest_artifact_status")],
            ["artifact time", g(legacyFixture, "latest_artifact_time_utc")],
            ["time source", g(legacyFixture, "latest_artifact_time_source")],
            ["summary status", g(legacyFixture, "latest_summary_status")],
          ]} />
          <FieldGrid rows={[
            ["real data backed", String(g(legacyFixture, "real_data_backed"))],
            ["claimed real data", String(g(legacyFixture, "claimed_real_data_backed"))],
            ["missing proof", g(legacyFixture, "missing_real_data_proof")],
            ["fixture backed", String(g(legacyFixture, "fixture_backed"))],
            ["structural only", String(g(legacyFixture, "structural_only"))],
            ["degraded", String(g(legacyFixture, "degraded"))],
            ["promotable", String(g(legacyFixture, "promotable"))],
            ["trade count", g(legacyFixture, "trade_count")],
            ["robustness", g(legacyFixture, "robustness_status")],
            ["robustness artifact", g(legacyFixture, "robustness_artifact")],
            ["failure notes", g(legacyFixture, "failure_notes")],
          ]} />
        </div>
        <div className="mt-3 text-sm text-ink-dim">{s(g(legacyFixture, "robustness_detail"))}</div>
      </Panel>

      <Panel title="Context Feature Coverage" right={<Badge tone="warn">{s(g(contextCoverage, "status"))}</Badge>}>
        <FieldGrid rows={[
          ["options context features", g(contextCoverage, "options_context_features")],
          ["standalone options", g(contextCoverage, "options_standalone_strategy")],
          ["note", g(contextCoverage, "note")],
        ]} />
      </Panel>
    </div>
  );
}
