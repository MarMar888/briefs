import { Clock3 } from "lucide-react";
import { getPendingDomains } from "@/lib/queries";

export const dynamic = "force-dynamic";

function daysUntil(value: string | null) {
  if (!value) return "-";
  const diff = new Date(value).getTime() - Date.now();
  return `${Math.ceil(diff / 86400000)}d`;
}

function dateText(value: string | null) {
  return value ? new Date(value).toLocaleDateString() : "-";
}

export default async function PendingPage() {
  const domains = await getPendingDomains();

  return (
    <section className="stack">
      <div className="pageHeader">
        <div>
          <h1>Pending Queue</h1>
          <p>Active domains still inside the 180-day tracking window.</p>
        </div>
        <div className="queueCount">
          <Clock3 size={18} />
          <strong>{domains.length}</strong>
        </div>
      </div>

      <div className="tableWrap">
        <table>
          <thead>
            <tr>
              <th>Domain</th>
              <th>Status</th>
              <th>Expires</th>
              <th>Next check</th>
              <th>Attempts</th>
              <th>DNS / Geo</th>
              <th>Last error</th>
            </tr>
          </thead>
          <tbody>
            {domains.map((domain) => (
              <tr key={domain.domain}>
                <td>
                  <a href={domain.websiteUrl || `https://${domain.domain}`} target="_blank" rel="noreferrer">
                    {domain.domain}
                  </a>
                  <small>seen {dateText(domain.firstSeenAt)}</small>
                </td>
                <td>
                  <span className={`status ${domain.status}`}>{domain.status.replace("_", " ")}</span>
                </td>
                <td>
                  <strong>{daysUntil(domain.expiresAt)}</strong>
                  <small>{dateText(domain.expiresAt)}</small>
                </td>
                <td>
                  <strong>{dateText(domain.nextCheckAt)}</strong>
                  <small>{domain.lastCheckedAt ? `checked ${dateText(domain.lastCheckedAt)}` : ""}</small>
                </td>
                <td>{domain.attemptCount}</td>
                <td>
                  <div className="contact">
                    <span>{domain.resolvedIp || "-"}</span>
                    <span>{domain.countryCode || ""}</span>
                  </div>
                </td>
                <td className="reason">{domain.lastError || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
