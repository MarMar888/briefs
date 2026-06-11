import { Filter } from "lucide-react";
import { ReviewControls } from "./review-controls";
import { getLeadStats, getMatchedLeads, type LeadFilters } from "@/lib/queries";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

function numberParam(value: string | undefined) {
  if (!value) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export default async function Page({ searchParams }: { searchParams?: Promise<SearchParams> | SearchParams }) {
  const params = await Promise.resolve(searchParams ?? {});
  const filters: LeadFilters = {
    minScore: numberParam(first(params.minScore)),
    maxScore: numberParam(first(params.maxScore)),
    ecomOnly: (first(params.ecomOnly) as LeadFilters["ecomOnly"]) || "all",
    reviewed: (first(params.reviewed) as LeadFilters["reviewed"]) || "approved"
  };

  const [stats, leads] = await Promise.all([getLeadStats(), getMatchedLeads(filters)]);

  return (
    <section className="stack">
      <div className="pageHeader">
        <div>
          <h1>Lead Dashboard</h1>
          <p>Matched domains sorted by current score.</p>
        </div>
      </div>

      <div className="stats">
        <div>
          <span>Total matched</span>
          <strong>{stats.totalMatched}</strong>
        </div>
        <div>
          <span>Pending review</span>
          <strong>{stats.pendingReview}</strong>
        </div>
        <div>
          <span>Average score</span>
          <strong>{stats.averageScore}</strong>
        </div>
      </div>

      <form className="filters">
        <Filter size={18} />
        <label>
          Min score
          <input name="minScore" type="number" min="0" max="100" defaultValue={filters.minScore ?? ""} />
        </label>
        <label>
          Max score
          <input name="maxScore" type="number" min="0" max="100" defaultValue={filters.maxScore ?? ""} />
        </label>
        <label>
          Ecommerce
          <select name="ecomOnly" defaultValue={filters.ecomOnly}>
            <option value="all">All</option>
            <option value="no">Physical/activity</option>
            <option value="yes">Ecom only</option>
          </select>
        </label>
        <label>
          Review
          <select name="reviewed" defaultValue={filters.reviewed}>
            <option value="all">All</option>
            <option value="no">Open</option>
            <option value="yes">Reviewed</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="starred">Starred</option>
          </select>
        </label>
        <button className="textButton" type="submit">
          Apply
        </button>
      </form>

      <div className="tableWrap">
        <table>
          <thead>
            <tr>
              <th>Domain</th>
              <th>Score</th>
              <th>Location</th>
              <th>Signals</th>
              <th>Contact</th>
              <th>Reason</th>
              <th>Review</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((lead) => (
              <tr key={lead.domain}>
                <td>
                  <a href={lead.websiteUrl || `https://${lead.domain}`} target="_blank" rel="noreferrer">
                    {lead.domain}
                  </a>
                  <small>{lead.classifiedAt ? new Date(lead.classifiedAt).toLocaleDateString() : ""}</small>
                </td>
                <td>
                  <span className={`score score${Math.floor((lead.score ?? 0) / 10)}`}>
                    {lead.score ?? "-"}
                  </span>
                  <small>{lead.scoreCategory}</small>
                </td>
                <td>{lead.location || "-"}</td>
                <td>
                  <div className="tags">
                    {lead.ecomOnly ? <span>Ecom</span> : <span>Physical</span>}
                    {lead.isTemplate ? <span>Template</span> : null}
                    {lead.established ? <span>Est. {lead.established}</span> : null}
                  </div>
                </td>
                <td>
                  <div className="contact">
                    <span>{lead.phone || "-"}</span>
                    <span>{lead.email || ""}</span>
                  </div>
                </td>
                <td className="reason">{lead.classificationReason}</td>
                <td>
                  <ReviewControls
                    domain={lead.domain}
                    initialVerdict={lead.humanVerdict}
                    initialNotes={lead.humanReviewNotes}
                    initialStarred={lead.starred ?? false}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
