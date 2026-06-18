import { useZones } from "../zonesContext";
import { Panel, Dot } from "../ui";
import type { Q001InventoryEvidence, SystemZone } from "../types";
import { gapSummary, records } from "./optionsDiagnostics";

function g(o: Record<string, unknown> | undefined, k: string): unknown {
  return o ? o[k] : undefined;
}
function s(v: unknown): string {
  return v === null || v === undefined ? "—" : String(v);
}

function Card({ title, status, rows }: { title: string; status?: string; rows: [string, unknown][] }) {
  return (
    <div className="panel p-4">
      <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-ink-dim">
        {status && <Dot status={status} />} {title}
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <div className="text-ink-faint">{k}</div>
            <div className="mono min-w-0 whitespace-normal break-words text-right text-ink">{s(v)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function LanesCard({ lanes }: { lanes: Record<string, unknown> | undefined }) {
  const items = (lanes ? (lanes["items"] as Record<string, unknown>[] | undefined) : undefined) ?? [];
  const status = lanes ? String(g(lanes, "status") ?? "unknown") : "missing";
  const rows: [string, unknown][] = items.map((it) => {
    const cp = it["capability_profile"] as Record<string, unknown> | undefined;
    const profileName = cp ? String(cp["name"] ?? "—") : "—";
    const researchOnly = cp && cp["research_only"] === true;
    return [String(it["lane"] ?? "?"), profileName + (researchOnly ? " (research-only)" : "")];
  });
  if (rows.length === 0) rows.push(["registered", "—"]);
  return <Card title="Lanes" status={status} rows={rows} />;
}

function OptionsDataCard({
  cod,
  defects,
}: {
  cod: Record<string, unknown> | undefined;
  defects: Record<string, unknown> | undefined;
}) {
  const status = cod ? String(g(cod, "status") ?? "unknown") : "missing";
  const defectStatus = defects ? String(g(defects, "status") ?? "unknown") : "missing";
  const openDefects = defects ? (g(defects, "open_count") ?? "—") : "—";
  const openIds = defects ? ((g(defects, "open_ids") as unknown[] | undefined)?.join(", ") ?? "—") : "—";
  const defectArtifact = defects ? g(defects, "artifact") : "—";
  const defectReason = defects ? g(defects, "reason") : "—";
  const summary = cod ? (cod["summary"] as Record<string, unknown> | undefined) : undefined;
  const expiryCoverage = summary ? (summary["expiry_coverage"] as Record<string, unknown> | undefined) : undefined;
  const checks = cod ? (cod["checks"] as Record<string, unknown>[] | undefined) ?? [] : [];
  const byName = (name: string) => checks.find((c) => String(c["name"] ?? "") === name);
  const fixingCheck = byName("options-fixing-mbo");
  const coverageCheck = byName("options-fixing-coverage");
  const strictMboCoverageCheck = byName("options-fixing-mbo-coverage");
  const ohlcvCheck = byName("options-ohlcv");
  const defsCheck = byName("options-definitions");
  const statsCheck = byName("options-statistics");
  const fixingDetail = fixingCheck ? String(fixingCheck["detail"] ?? "—") : "—";
  const ohlcvDetail = ohlcvCheck ? String(ohlcvCheck["detail"] ?? "—") : "—";
  const defsDetail = defsCheck ? String(defsCheck["detail"] ?? "—") : "—";
  const statsDetail = statsCheck ? String(statsCheck["detail"] ?? "—") : "—";
  const coverageDetail = coverageCheck ? String(coverageCheck["detail"] ?? "—") : "—";
  const strictMboDetail = strictMboCoverageCheck ? String(strictMboCoverageCheck["detail"] ?? "—") : "—";
  const gapCount = coverageCheck?.["gap_count"] ?? expiryCoverage?.["gap_count"] ?? expiryCoverage?.["gaps"];
  const staleGapCount = coverageCheck?.["stale_gap_count"] ?? expiryCoverage?.["stale_gap_count"];
  const gapDiagnostics = records(expiryCoverage?.["gap_diagnostics"]);
  const gapsDetail = coverageCheck || expiryCoverage ? `${s(gapCount ?? "—")} gaps / ${s(staleGapCount ?? "—")} stale` : "—";
  const coverageFallback = expiryCoverage
    ? `${s(expiryCoverage["expected_dates"])} expected / ${s(gapCount ?? "—")} gaps`
    : "—";
  const summaryNote = coverageDetail !== "—" ? coverageDetail : coverageFallback;
  return (
    <Card title="Options data (CME)" status={status} rows={[
      ["fixing files", fixingDetail],
      ["coverage", summaryNote],
      ["strict MBO", strictMboDetail],
      ["gaps", gapsDetail],
      ["first gap", gapSummary(gapDiagnostics[0])],
      ["ohlcv", ohlcvDetail],
      ["definitions", defsDetail],
      ["statistics", statsDetail],
      ["open defects", `${s(openDefects)} (${defectStatus})`],
      ["defect ids", openIds],
      ["defect artifact", defectArtifact],
      ["defect reason", defectReason],
      ["report age", cod ? s(g(cod, "report_age")) : "—"],
    ]} />
  );
}

function Q001InventoryCard({ q001 }: { q001: Q001InventoryEvidence | undefined }) {
  const status = q001 ? String(q001.status ?? "unknown") : "missing";
  const gaps = Array.isArray(q001?.gaps) ? q001.gaps : [];
  const gapRecords = records(q001?.gaps);
  const gapCount = q001 ? q001["gap_count"] ?? gaps.length : undefined;
  const firstGap = gapRecords[0];
  const firstGapLabel = firstGap ? [firstGap["source"], firstGap["severity"]].filter(Boolean).join(" ") : "";
  const gapText = firstGap
    ? String(firstGap["detail"] ?? (firstGapLabel || gapSummary(firstGap)))
    : gaps.length ? `${gaps.length} gaps` : "—";
  const acceptedEvidence = q001?.accepted_evidence;
  const modelGapPolicy = q001?.model_gap_policy;
  const validationErrors = Array.isArray(q001?.owner_decision_validation_errors)
    ? q001.owner_decision_validation_errors.join(", ")
    : "—";
  const acceptedScope = q001?.available_data_scope_accepted === true
    ? "accepted; missing-data models sidelined"
    : q001 ? "not accepted" : "—";
  const acceptedCounts = acceptedEvidence
    ? `mbo=${s(acceptedEvidence["missing_or_unavailable_slots"])}, strict=${s(acceptedEvidence["strict_mbo_gap_count"])}/${s(acceptedEvidence["strict_mbo_stale_gap_count"])}`
    : "—";
  const gapPolicy = modelGapPolicy
    ? `${s(modelGapPolicy["available_data_models"])}; ${s(modelGapPolicy["missing_mbo_required_models"])}`
    : "—";
  return (
    <Card title="Q001 paid-data inventory" status={status} rows={[
      ["q001_status", q001?.q001_status],
      ["owner decision", q001?.owner_decision_status],
      ["available-data scope", acceptedScope],
      ["decision artifact", q001?.owner_decision_artifact],
      ["accepted evidence", acceptedCounts],
      ["model gap policy", gapPolicy],
      ["decision validation", validationErrors],
      ["artifact", q001?.artifact],
      ["MBO missing/unavailable slots", q001?.missing_or_unavailable_slots],
      ["options doctor", q001?.data_doctor_status],
      ["strict MBO gaps", q001?.strict_mbo_gap_count],
      ["strict MBO stale gaps", q001?.strict_mbo_stale_gap_count],
      ["gap count", gapCount],
      ["gap summary", gapText],
    ]} />
  );
}

export function SystemView() {
  const sys = useZones().system as SystemZone | undefined;
  if (!sys) return <Panel title="System"><span className="text-ink-dim">loading…</span></Panel>;
  const lat = sys.latency, cert = sys.certification, slow = sys.slow_tier, db = sys.databento, cap = sys.capture, ex = sys.execution;
  const lanes = sys.lanes as Record<string, unknown> | undefined;
  const cod = lanes ? (lanes["cme_options_data"] as Record<string, unknown> | undefined) : undefined;
  const defects = lanes ? (lanes["cme_options_defects"] as Record<string, unknown> | undefined) : undefined;
  const gaps = sys.health_gaps as Record<string, unknown> | undefined;
  const docsPresent = gaps?.docs_present as Record<string, boolean> | undefined;
  const validationStatus = docsPresent?.validation_honesty === false ? "fail" : docsPresent ? "ok" : "unknown";
  const latencyStatus = String(g(lat, "status") ?? "unknown");
  const latencyLiveArm = String(g(lat, "live_arm_status") ?? "unknown");
  const latencyBottleneck = g(lat, "dominant_bottleneck");
  const slowStatus = String(g(slow, "status") ?? "unknown");
  const slowReview = String(g(slow, "review_status") ?? "unknown");
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Card title="Latency" status={latencyStatus} rows={[
        ["order-ack p99", g(lat, "order_ack_p99_ms") != null ? `${Number(g(lat, "order_ack_p99_ms")).toFixed(2)} ms` : "—"],
        ["lane", `${s(g(lat, "recommended_lane"))} ${s(g(lat, "lane_name"))}`],
        ["research verdict", latencyStatus === "ok" ? "ok" : g(lat, "overall")],
        ["live arm", latencyLiveArm === "fail" ? "blocked until live network path is cleared" : latencyLiveArm],
        ["live-arm note", latencyBottleneck],
      ]} />
      <Card title="Certification" status={String(g(cert, "status") ?? "unknown")} rows={[
        ["status", g(cert, "certification_status")],
        ["blocking", (g(cert, "blocking_failures") as unknown[] | undefined)?.length ?? 0],
        ["exec modes", (g(cert, "execution_modes") as string[] | undefined)?.join(",")],
      ]} />
      <Card title="Slow-tier LLM" status={slowStatus} rows={[
        ["problems", g(slow, "n_problems")],
        ["research gate", slowStatus === "ok" ? "ok" : g(slow, "problems_age")],
        ["review ledger", slowReview === "fail" ? "queued for review" : slowReview],
      ]} />
      <Card title="Databento" status={String(g(db, "status") ?? "unknown")} rows={[
        ["used", g(db, "total_used") != null ? `$${Number(g(db, "total_used")).toFixed(2)}` : "—"],
        ["remaining", g(db, "remaining") != null ? `$${Number(g(db, "remaining")).toFixed(2)}` : "—"],
        ["manifest rows", g(db, "manifest_rows")],
      ]} />
      <Card title="Capture" status={String(g(cap, "status") ?? "unknown")} rows={[
        ["host", g(cap, "host")],
        ["known gaps", (g(cap, "known_gaps") as unknown[] | undefined)?.length ?? 0],
      ]} />
      <Card title="Execution" status={String(g(ex, "live")) === "true" ? "fail" : "ok"} rows={[
        ["mode", g(ex, "execution_mode")], ["kill switch", g(ex, "kill_switch")], ["live", String(g(ex, "live"))],
      ]} />
      <LanesCard lanes={lanes} />
      <OptionsDataCard cod={cod} defects={defects} />
      <Q001InventoryCard q001={sys.q001_inventory} />
      <Card title="Repo context" status="ok" rows={[
        ["canonical path", (sys.repo_context as Record<string, unknown> | undefined)?.canonical_path],
        ["branch", (sys.repo_context as Record<string, unknown> | undefined)?.branch],
        ["commit", (sys.repo_context as Record<string, unknown> | undefined)?.commit],
        ["REPO_STATE", (sys.repo_context as Record<string, unknown> | undefined)?.repo_state_artifact],
        ["HEAD summary", (sys.repo_context as Record<string, unknown> | undefined)?.head_summary],
        ["secondary workspace", (sys.repo_context as Record<string, unknown> | undefined)?.secondary_workspace_note],
      ]} />
      <Card title="Validation honesty" status={validationStatus} rows={[
        ["charter", (sys.health_gaps as Record<string, unknown> | undefined)?.validation_honesty_artifact ?? "docs/VALIDATION_HONESTY.md"],
        ["repo state doc", (sys.health_gaps as Record<string, unknown> | undefined)?.repo_state_artifact],
        ["M6 monitor doc", (sys.health_gaps as Record<string, unknown> | undefined)?.universe_monitor_artifact ?? "—"],
        ["note", (sys.health_gaps as Record<string, unknown> | undefined)?.note],
      ]} />
    </div>
  );
}
