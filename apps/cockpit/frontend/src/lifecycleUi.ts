import type { LifecycleRow } from "./types";

export function lifecycleSubmitTone(
  row: {
    state?: string | null;
    submit_allowed?: boolean;
    submit_size_factor?: number;
    latest_model_state?: string | null;
  },
): "ok" | "warn" | "bad" | "dim" {
  const state = row.state ?? "";
  if (state === "QUARANTINED" || state === "RETIRED") return "bad";
  if (!row.submit_allowed) return "bad";
  if ((row.submit_size_factor ?? 1) < 1) return "warn";
  if (state === "LIVE" && row.latest_model_state === "GREEN") return "ok";
  if (state === "LIVE" || state === "DEGRADED") return "warn";
  return "dim";
}

export function lifecycleSubmitLabel(
  row: Pick<LifecycleRow, "submit_allowed" | "submit_size_factor">,
): string {
  if (!row.submit_allowed) return "blocked";
  const sf = row.submit_size_factor ?? 1;
  if (sf < 1) return `size ${Math.round(sf * 100)}%`;
  return "allowed";
}

export function lifecycleLiveKpiTone(
  rows: LifecycleRow[],
  liveCount: number,
): "ok" | "warn" | "dim" {
  if (liveCount <= 0) return "dim";
  const anyDecay = rows.some(
    (r) =>
      r.state === "LIVE"
      && (r.latest_model_state !== "GREEN" || (r.submit_size_factor ?? 1) < 1),
  );
  return anyDecay ? "warn" : "ok";
}
