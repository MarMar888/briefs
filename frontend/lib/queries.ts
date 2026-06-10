import { and, avg, count, desc, eq, gte, inArray, lte, sql } from "drizzle-orm";
import { db } from "./db";
import { domains, pipelineRuns } from "./schema";

export type LeadFilters = {
  minScore?: number;
  maxScore?: number;
  ecomOnly?: "all" | "yes" | "no";
  reviewed?: "all" | "yes" | "no";
};

export async function getLeadStats() {
  const [matched] = await db
    .select({
      total: count(),
      averageScore: avg(domains.score)
    })
    .from(domains)
    .where(eq(domains.status, "matched"));

  const [pendingReview] = await db
    .select({ total: count() })
    .from(domains)
    .where(and(eq(domains.status, "matched"), eq(domains.humanReviewed, false)));

  return {
    totalMatched: Number(matched?.total ?? 0),
    pendingReview: Number(pendingReview?.total ?? 0),
    averageScore: Math.round(Number(matched?.averageScore ?? 0))
  };
}

export async function getMatchedLeads(filters: LeadFilters) {
  const clauses = [eq(domains.status, "matched")];

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
  }
  if (filters.reviewed === "no") {
    clauses.push(eq(domains.humanReviewed, false));
  }

  return db
    .select()
    .from(domains)
    .where(and(...clauses))
    .orderBy(desc(domains.score), desc(domains.classifiedAt))
    .limit(250);
}

export async function getPendingDomains() {
  return db
    .select()
    .from(domains)
    .where(inArray(domains.status, ["new", "geo_pending", "site_pending"]))
    .orderBy(sql`${domains.expiresAt} asc nulls last`, sql`${domains.nextCheckAt} asc nulls first`)
    .limit(500);
}

export async function getPipelineRuns(limit = 100) {
  return db
    .select()
    .from(pipelineRuns)
    .orderBy(desc(pipelineRuns.startedAt))
    .limit(limit);
}

export async function reviewDomain(domain: string, verdict: "approved" | "rejected", notes: string) {
  await db
    .update(domains)
    .set({
      humanReviewed: true,
      humanVerdict: verdict,
      humanReviewNotes: notes,
      lastSeenAt: new Date().toISOString()
    })
    .where(eq(domains.domain, domain));
}
