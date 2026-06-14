export function s(v: unknown): string {
  if (Array.isArray(v)) return v.length ? v.join(", ") : "-";
  return v === null || v === undefined ? "-" : String(v);
}

export function records(v: unknown): Record<string, unknown>[] {
  return Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === "object") : [];
}

export function invalidArtifactSummary(row: Record<string, unknown>): string {
  const invalid = records(row["invalid_artifacts"]);
  if (!invalid.length) return "-";
  const first = invalid[0];
  const name = first["file"] ?? first["path"] ?? first["source"] ?? "artifact";
  const reason = first["reason"] ?? first["message"] ?? row["reason"];
  const suffix = invalid.length > 1 ? ` +${invalid.length - 1}` : "";
  return `${s(name)}: ${s(reason)}${suffix}`;
}

export function gapSummary(row: Record<string, unknown> | undefined): string {
  if (!row) return "-";
  const date = s(row["date"]);
  const reason = s(row["reason"]);
  const action = s(row["required_action"]);
  return `${date} ${reason} ${action}`;
}
