import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { X, BookOpen, ExternalLink, FileText } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { apiGet, openArtifact } from "../api";
import { Badge } from "../ui";
import type { ModelDetail as MD, ModelResultSurface } from "../modelTypes";

const AX = { stroke: "#6b7689", fontSize: 11 };

function Chips({ label, items, tone = "dim" }: { label: string; items: string[]; tone?: "dim" | "accent" | "warn" }) {
  if (!items?.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] uppercase tracking-wide text-ink-faint">{label}</span>
      {items.map((x) => <Badge key={x} tone={tone}>{x}</Badge>)}
    </div>
  );
}

function surfacePath(s: ModelResultSurface): string | null {
  return s.artifact_path || s.artifact || s.path || null;
}

function surfaceViewerUrl(s: ModelResultSurface): string | null {
  return (
    s.viewer_url ||
    s.viewerUrl ||
    s.external_viewer_url ||
    s.external_url ||
    s.externalUrl ||
    (s.url?.startsWith("http") ? s.url : null)
  );
}

function surfaceLabel(s: ModelResultSurface): string {
  return s.label || s.title || s.name || s.kind || surfacePath(s) || "artifact";
}

function SurfaceActions({ surfaces }: { surfaces: ModelResultSurface[] }) {
  const actions = surfaces
    .map((s, i) => {
      const viewer = surfaceViewerUrl(s);
      const path = surfacePath(s);
      return viewer || path ? { viewer, path, label: surfaceLabel(s), key: `${viewer || path}:${i}` } : null;
    })
    .filter((x): x is { viewer: string | null; path: string | null; label: string; key: string } => Boolean(x));

  if (!actions.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] uppercase tracking-wide text-ink-faint">surfaces</span>
      {actions.map((a) => {
        if (a.viewer) {
          return (
            <a key={a.key} href={a.viewer} target="_blank" rel="noreferrer"
              className="chip hover:border-accent/60 hover:text-accent" title={a.viewer}>
              <ExternalLink size={12} />
              <span>{a.label}</span>
            </a>
          );
        }
        return (
          <button key={a.key} type="button" onClick={() => a.path && void openArtifact(a.path)}
            className="chip hover:border-accent/60 hover:text-accent" title={a.path ?? undefined}>
            <FileText size={12} />
            <span>{a.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function ModelDetail({ hypId, open, onClose }: { hypId: number | null; open: boolean; onClose: () => void }) {
  const [d, setD] = useState<MD | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!open || hypId == null) return;
    setLoading(true); setD(null);
    apiGet<MD>(`/api/model/${hypId}`)
      .then((r) => setD(r))
      .catch((e) => setD({ id: hypId, error: String(e) } as MD))
      .finally(() => setLoading(false));
  }, [open, hypId]);

  const c = d?.construction;
  const cells = d?.results?.stage_a_cells ?? [];
  const surfaces = d?.results?.surfaces ?? [];
  const chart = cells.map((x) => ({ e: x.event_type, v: x.mean_expectancy_usd ?? 0 }))
    .sort((a, b) => b.v - a.v).slice(0, 24);

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content className="panel fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[min(900px,94vw)] -translate-x-1/2 -translate-y-1/2 overflow-hidden">
          <div className="flex items-center gap-3 border-b border-line px-5 py-3">
            <Dialog.Title className="text-base font-semibold">
              {d ? <>#{d.id} · {d.name}</> : "Loading…"}
            </Dialog.Title>
            {d && <Badge tone={d.family === "prop" ? "warn" : d.family === "vix" ? "accent" : "dim"}>{d.family}</Badge>}
            {d?.structurally_dead && <Badge tone="bad">structurally dead</Badge>}
            <Dialog.Close className="ml-auto rounded p-1 text-ink-dim hover:bg-bg-elev"><X size={18} /></Dialog.Close>
          </div>

          {loading && <div className="p-6 text-ink-dim">fetching model detail…</div>}
          {!loading && d?.error && (
            <div className="p-6 text-sm text-bad">Failed to load model #{d.id}: {d.error}</div>
          )}
          {d && !d.error && (
            <Tabs.Root defaultValue="construction" className="flex max-h-[78vh] flex-col">
              <Tabs.List className="flex gap-1 border-b border-line px-4">
                {["construction", "results"].map((t) => (
                  <Tabs.Trigger key={t} value={t}
                    className="px-3 py-2 text-sm capitalize text-ink-dim data-[state=active]:border-b-2 data-[state=active]:border-accent data-[state=active]:text-ink">
                    {t}
                  </Tabs.Trigger>
                ))}
              </Tabs.List>

              <div className="scroll-area overflow-auto p-5">
                <Tabs.Content value="construction" className="space-y-4">
                  {c?.defect_note && <div className="panel border-bad/40 p-3 text-sm text-bad">{c.defect_note}</div>}
                  {c?.thesis && <p className="text-sm leading-relaxed text-ink-dim whitespace-pre-line">{c.thesis}</p>}
                  {c && (
                    <div className="panel border-accent/30 p-3 text-sm">
                      <div className="mb-1 text-[11px] uppercase tracking-wide text-ink-faint">how it works</div>
                      {c.how_it_works}
                    </div>
                  )}
                  <Chips label="features" items={c?.features ?? []} tone="accent" />
                  <Chips label="event gates" items={c?.event_gates ?? []} tone="warn" />
                  <Chips label="regime gates" items={c?.regime_gates ?? []} tone="warn" />
                  <Chips label="cross-asset" items={c?.cross_assets ?? []} tone="dim" />
                  {!!c?.citations?.length && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <BookOpen size={13} className="text-ink-faint" />
                      {c.citations.map((x) => <Badge key={x} tone="accent">{x}</Badge>)}
                    </div>
                  )}
                  {c?.evaluate_source && (
                    <details className="panel p-0">
                      <summary className="cursor-pointer px-3 py-2 text-[11px] uppercase tracking-wide text-ink-dim">signal source — evaluate()</summary>
                      <pre className="scroll-area overflow-auto border-t border-line p-3 text-[12px] leading-relaxed text-ink-dim mono">{c.evaluate_source}</pre>
                    </details>
                  )}
                  {!c && <div className="text-ink-faint">No source construction found for this id.</div>}
                </Tabs.Content>

                <Tabs.Content value="results" className="space-y-4">
                  <div className="text-sm text-ink-dim">{d.results?.n_event_types ?? 0} event types · {d.results?.total_trades ?? 0} total trades</div>
                  <SurfaceActions surfaces={surfaces} />
                  {chart.length > 0 && (
                    <div className="panel p-3">
                      <div className="mb-2 text-[11px] uppercase tracking-wide text-ink-faint">mean expectancy by event type (USD)</div>
                      <ResponsiveContainer width="100%" height={Math.min(360, 28 + chart.length * 16)}>
                        <BarChart data={chart} layout="vertical" margin={{ left: 30 }}>
                          <XAxis type="number" {...AX} /><YAxis type="category" dataKey="e" width={150} {...AX} />
                          <Tooltip contentStyle={{ background: "#141926", border: "1px solid #222a3a", borderRadius: 8 }} />
                          <Bar dataKey="v" radius={[0, 3, 3, 0]}>
                            {chart.map((x, i) => <Cell key={i} fill={x.v >= 0 ? "#3fb950" : "#f0556a"} />)}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                  <div className="panel p-0">
                    <table className="w-full text-sm">
                      <thead><tr><th className="th">event type</th><th className="th text-right">trades</th><th className="th text-right">events</th><th className="th text-right">mean E[$]</th><th className="th text-right">win rate</th><th className="th text-right">tail</th></tr></thead>
                      <tbody>
                        {cells.map((x, i) => (
                          <tr key={i}>
                            <td className="td">{x.event_type}</td>
                            <td className="td text-right mono">{x.total_trades}</td>
                            <td className="td text-right mono">{x.n_events}</td>
                            <td className={`td text-right mono ${(x.mean_expectancy_usd ?? 0) >= 0 ? "text-ok" : "text-bad"}`}>{x.mean_expectancy_usd?.toFixed(2) ?? "—"}</td>
                            <td className="td text-right mono">{x.mean_win_rate != null ? (x.mean_win_rate * 100).toFixed(1) + "%" : "—"}</td>
                            <td className="td text-right mono text-bad">{x.p5_expectancy_tail_usd?.toFixed(2) ?? "—"}</td>
                          </tr>
                        ))}
                        {cells.length === 0 && <tr><td className="td text-ink-faint" colSpan={6}>no Stage A cells for this model yet</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </Tabs.Content>
              </div>
            </Tabs.Root>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
