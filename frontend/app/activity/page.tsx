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

export default async function ActivityPage() {
  const runs = await getPipelineRuns(100);

  return (
    <section className="stack">
      <div className="pageHeader">
        <div>
          <h1>Pipeline Activity</h1>
          <p>Recent backfill and live scan runs.</p>
        </div>
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
              <th>Matched</th>
              <th>Expired</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan={8} style={{ color: "var(--muted)", textAlign: "center", padding: "32px" }}>
                  No runs yet. Start the pipeline to see activity here.
                </td>
              </tr>
            )}
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{formatDate(run.startedAt)}</td>
                <td>{formatSource(run.source)}</td>
                <td>{formatDuration(run.startedAt, run.finishedAt)}</td>
                <td>{run.downloaded ?? "-"}</td>
                <td>{run.inserted ?? "-"}</td>
                <td>{run.matched ?? "-"}</td>
                <td>{run.expired ?? "-"}</td>
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
