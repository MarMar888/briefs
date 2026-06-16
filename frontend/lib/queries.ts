import { and, avg, count, desc, eq, gte, inArray, lte, sql } from "drizzle-orm";
import { getDb } from "./db";
import { domains, pipelineRuns } from "./schema";

export type LeadFilters = {
  minScore?: number;
  maxScore?: number;
  ecomOnly?: "all" | "yes" | "no";
  reviewed?: "all" | "yes" | "no" | "approved" | "rejected" | "starred";
  audit?: "active" | "all" | "qualified" | "filtered" | "unaudited";
  industry?: string;
};

export async function getLeadStats(industry?: string) {
  const db = getDb();
  const industryClause = industry ? eq(domains.industry, industry) : undefined;
  const [matched] = await db
    .select({
      total: count(),
      averageScore: avg(domains.score)
    })
    .from(domains)
    .where(and(eq(domains.status, "matched"), industryClause));

  const [pendingReview] = await db
    .select({ total: count() })
    .from(domains)
    .where(and(eq(domains.status, "matched"), eq(domains.humanReviewed, false), industryClause));

  return {
    totalMatched: Number(matched?.total ?? 0),
    pendingReview: Number(pendingReview?.total ?? 0),
    averageScore: Math.round(Number(matched?.averageScore ?? 0))
  };
}

export async function getMatchedLeads(filters: LeadFilters) {
  const db = getDb();
  // All leads stay status=matched; the audit labels them (it never deletes).
  // Default "active" hides audit-disqualified leads (established / side project);
  // "filtered" surfaces exactly those for inspection.
  const clauses = [eq(domains.status, "matched")];

  if (filters.industry) {
    clauses.push(eq(domains.industry, filters.industry));
  }

  if (filters.audit === "filtered") {
    clauses.push(eq(domains.auditVerdict, "disqualified"));
  } else if (filters.audit === "qualified") {
    clauses.push(eq(domains.auditVerdict, "qualified"));
  } else if (filters.audit === "unaudited") {
    clauses.push(sql`${domains.enrichedAt} is null`);
  } else if (filters.audit !== "all") {
    clauses.push(sql`(${domains.auditVerdict} is null or ${domains.auditVerdict} <> 'disqualified')`);
  }

  if (typeof filters.minScore === "number") {
    clauses.push(gte(domains.score, filters.minScore));
  }
  if (typeof filters.maxScore === "number") {
    clauses.push(lte(domains.score, filters.maxScore));
  }
  if (filters.ecomOnly === "yes") {
    clauses.push(eq(domains.ecomOnly, true));
  }
  if (filters.ecomOnly === "no") {
    clauses.push(eq(domains.ecomOnly, false));
  }
  if (filters.reviewed === "yes") {
    clauses.push(eq(domains.humanReviewed, true));
  } else if (filters.reviewed === "no") {
    clauses.push(eq(domains.humanReviewed, false));
  } else if (filters.reviewed === "approved") {
    clauses.push(eq(domains.humanVerdict, "approved"));
  } else if (filters.reviewed === "rejected") {
    clauses.push(eq(domains.humanVerdict, "rejected"));
  } else if (filters.reviewed === "starred") {
    clauses.push(eq(domains.starred, true));
  }

  return db
    .select()
    .from(domains)
    .where(and(...clauses))
    .orderBy(desc(domains.score), desc(domains.classifiedAt))
    .limit(250);
}

export async function getPendingDomains(industry?: string) {
  return getDb()
    .select()
    .from(domains)
    .where(
      and(
        inArray(domains.status, ["new", "geo_pending", "site_pending"]),
        industry ? eq(domains.industry, industry) : undefined
      )
    )
    .orderBy(sql`${domains.expiresAt} asc nulls last`, sql`${domains.nextCheckAt} asc nulls first`)
    .limit(500);
}

export async function getPipelineInventory(industry?: string) {
  const db = getDb();
  const industryClause = industry ? eq(domains.industry, industry) : undefined;

  const statusRows = await db
    .select({ status: domains.status, count: count() })
    .from(domains)
    .where(industryClause)
    .groupBy(domains.status);

  const categoryRows = await db
    .select({ scoreCategory: domains.scoreCategory, count: count() })
    .from(domains)
    .where(and(eq(domains.status, "matched"), industryClause))
    .groupBy(domains.scoreCategory);

  const statusMap = Object.fromEntries(statusRows.map((r) => [r.status, Number(r.count)]));
  const categoryMap = Object.fromEntries(categoryRows.map((r) => [r.scoreCategory ?? "Unknown", Number(r.count)]));

  return { statusMap, categoryMap };
}

export async function getPipelineRuns(limit = 100, industry?: string) {
  return getDb()
    .select()
    .from(pipelineRuns)
    .where(industry ? eq(pipelineRuns.industry, industry) : undefined)
    .orderBy(desc(pipelineRuns.startedAt))
    .limit(limit);
}

export async function toggleStar(domain: string, starred: boolean) {
  await getDb()
    .update(domains)
    .set({ starred, lastSeenAt: new Date().toISOString() })
    .where(eq(domains.domain, domain));
}

export async function reviewDomain(domain: string, verdict: "approved" | "rejected" | null, notes: string) {
  if (verdict !== null) {
    await getDb()
      .update(domains)
      .set({ humanReviewed: true, humanVerdict: verdict, humanReviewNotes: notes, lastSeenAt: new Date().toISOString() })
      .where(eq(domains.domain, domain));
  } else {
    await getDb()
      .update(domains)
      .set({ humanReviewNotes: notes, lastSeenAt: new Date().toISOString() })
      .where(eq(domains.domain, domain));
  }
}
