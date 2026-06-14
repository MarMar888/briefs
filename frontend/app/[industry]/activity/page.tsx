import { getPipelineRuns } from "@/lib/queries";

export const dynamic = "force-dynamic";

const SOURCE_LABELS: Record<string, string> = {
  domainsmonitor: "Live",
  "domainsmonitor-file": "Backfill",
  domainkits: "DomainKits",
  "domainkits-file": "DomainKits File",
  whoisds: "WhoisDS",
};

function formatSource(source: string | null) {
  if (!source) return "-";
  return SOURCE_LABELS[source] ?? source;
}

function formatDuration(startedAt: string, finishedAt: string | null) {
  if (!finishedAt) return "Running…";
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

function formatDate(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function pct(num: number | null | undefined, den: number | null | undefined) {
  if (!num || !den) return null;
  return Math.round((num / den) * 100);
}

function PctCell({ num, den }: { num: number | null | undefined; den: number | null | undefined }) {
  const p = pct(num, den);
  if (p === null) return <td style={{ color: "var(--muted)" }}>—</td>;
  const color = p >= 15 ? "var(--green)" : p >= 7 ? "var(--amber)" : "var(--muted)";
  return (
    <td style={{ color, fontVariantNumeric: "tabular-nums" }}>
      {p}%<small style={{ color: "var(--muted)", marginLeft: 3 }}>({num}/{den})</small>
    </td>
  );
}

export default async function ActivityPage({ params }: { params: Promise<{ industry: string }> }) {
  const { industry } = await params;
  const runs = await getPipelineRuns(100, industry);

  // Aggregate stats over runs that had classification work
  const classifyRuns = runs.filter((r) => (r.siteProcessed ?? 0) > 0);
  const kwRuns = classifyRuns.filter((r) => (r.keywordProcessed ?? 0) > 0);
  const randRuns = classifyRuns.filter((r) => (r.randomProcessed ?? 0) > 0);

  const avgHitRate = classifyRuns.length
    ? Math.round(
        classifyRuns.reduce((s, r) => s + (r.matched ?? 0) / (r.siteProcessed ?? 1), 0) /
          classifyRuns.length * 100
      )
    : null;
  const avgKwRate = kwRuns.length
    ? Math.round(
        kwRuns.reduce((s, r) => s + (r.keywordMatched ?? 0) / (r.keywordProcessed ?? 1), 0) /
          kwRuns.length * 100
      )
    : null;
  const avgRandRate = randRuns.length
    ? Math.round(
        randRuns.reduce((s, r) => s + (r.randomMatched ?? 0) / (r.randomProcessed ?? 1), 0) /
          randRuns.length * 100
      )
    : null;

  const totalMatchedRecent = classifyRuns.slice(0, 20).reduce((s, r) => s + (r.matched ?? 0), 0);

  return (
    <section className="stack">
      <div className="pageHeader">
        <div>
          <h1>Pipeline Activity</h1>
          <p>Sourcing quality and run history.</p>
        </div>
      </div>

      {/* Sourcing quality summary */}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <StatCard label="Avg hit rate" value={avgHitRate !== null ? `${avgHitRate}%` : "—"} sub="matched / sites checked" />
        <StatCard label="Keyword precision" value={avgKwRate !== null ? `${avgKwRate}%` : "—"} sub="keyword-targeted match rate" />
        <StatCard label="Random baseline" value={avgRandRate !== null ? `${avgRandRate}%` : "—"} sub="random sample match rate" />
        <StatCard
          label="Keyword lift"
          value={avgKwRate !== null && avgRandRate !== null && avgRandRate > 0
            ? `${(avgKwRate / avgRandRate).toFixed(1)}×`
            : "—"}
          sub="kw precision ÷ random"
        />
        <StatCard label="Matched (last 20 runs)" value={String(totalMatchedRecent)} sub="cumulative" />
      </div>

      <div className="tableWrap activityTable">
        <table>
          <thead>
            <tr>
              <th>Started</th>
              <th>Source</th>
              <th>Duration</th>
              <th>Downloaded</th>
              <th>Inserted</th>
              <th>Checked</th>
              <th>Hit rate</th>
              <th>Kw %</th>
              <th>Rand %</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan={10} style={{ color: "var(--muted)", textAlign: "center", padding: "32px" }}>
                  No runs yet. Start the pipeline to see activity here.
                </td>
              </tr>
            )}
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{formatDate(run.startedAt)}</td>
                <td>{formatSource(run.source)}</td>
                <td>{formatDuration(run.startedAt, run.finishedAt)}</td>
                <td>{run.downloaded ?? "—"}</td>
                <td>{run.inserted ?? "—"}</td>
                <td>{run.siteProcessed ?? "—"}</td>
                <PctCell num={run.matched} den={run.siteProcessed} />
                <PctCell num={run.keywordMatched} den={run.keywordProcessed} />
                <PctCell num={run.randomMatched} den={run.randomProcessed} />
                <td>
                  <span className={`status ${run.status}`}>{run.status}</span>
                  {run.error && (
                    <small style={{ display: "block", marginTop: 4, color: "var(--red)" }}>
                      {run.error.slice(0, 120)}
                    </small>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div style={{
      background: "var(--panel)",
      border: "1px solid var(--line)",
      borderRadius: 8,
      padding: "12px 16px",
      minWidth: 140,
    }}>
      <div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 600, lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{sub}</div>
    </div>
  );
}
