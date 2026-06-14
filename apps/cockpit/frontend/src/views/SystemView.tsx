import { useZones } from "../zonesContext";
import { Panel, Dot } from "../ui";
import type { SystemZone } from "../types";

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
            <div className="mono text-right text-ink">{s(v)}</div>
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
  const ohlcvCheck = byName("options-ohlcv");
  const defsCheck = byName("options-definitions");
  const statsCheck = byName("options-statistics");
  const fixingDetail = fixingCheck ? String(fixingCheck["detail"] ?? "—") : "—";
  const ohlcvDetail = ohlcvCheck ? String(ohlcvCheck["detail"] ?? "—") : "—";
  const defsDetail = defsCheck ? String(defsCheck["detail"] ?? "—") : "—";
  const statsDetail = statsCheck ? String(statsCheck["detail"] ?? "—") : "—";
  const coverageDetail = coverageCheck ? String(coverageCheck["detail"] ?? "—") : "—";
  const gapCount = coverageCheck?.["gap_count"] ?? expiryCoverage?.["gap_count"] ?? expiryCoverage?.["gaps"];
  const staleGapCount = coverageCheck?.["stale_gap_count"] ?? expiryCoverage?.["stale_gap_count"];
  const gapsDetail = coverageCheck || expiryCoverage ? `${s(gapCount ?? "—")} gaps / ${s(staleGapCount ?? "—")} stale` : "—";
  const coverageFallback = expiryCoverage
    ? `${s(expiryCoverage["expected_dates"])} expected / ${s(gapCount ?? "—")} gaps`
    : "—";
  const summaryNote = coverageDetail !== "—" ? coverageDetail : coverageFallback;
  return (
    <Card title="Options data (CME)" status={status} rows={[
      ["fixing files", fixingDetail],
      ["coverage", summaryNote],
      ["gaps", gapsDetail],
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

export function SystemView() {
  const sys = useZones().system as SystemZone | undefined;
  if (!sys) return <Panel title="System"><span className="text-ink-dim">loading…</span></Panel>;
  const lat = sys.latency, cert = sys.certification, slow = sys.slow_tier, db = sys.databento, cap = sys.capture, ex = sys.execution;
  const lanes = sys.lanes as Record<string, unknown> | undefined;
  const cod = lanes ? (lanes["cme_options_data"] as Record<string, unknown> | undefined) : undefined;
  const defects = lanes ? (lanes["cme_options_defects"] as Record<string, unknown> | undefined) : undefined;
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
    </div>
  );
}
