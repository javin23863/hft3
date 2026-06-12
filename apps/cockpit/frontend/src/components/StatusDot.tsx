export function StatusDot({ status }: { status: string }) {
  return <span className={`dot ${status}`} title={status} />;
}
