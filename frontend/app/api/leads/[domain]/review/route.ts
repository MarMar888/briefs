import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { reviewDomain } from "@/lib/queries";

export async function PATCH(request: NextRequest, { params }: { params: Promise<{ domain: string }> }) {
  const { domain } = await params;
  const body = await request.json().catch(() => null);
  const verdict = body?.verdict;
  const notes = typeof body?.notes === "string" ? body.notes : "";

  if (verdict !== "approved" && verdict !== "rejected") {
    return NextResponse.json({ error: "verdict must be approved or rejected" }, { status: 400 });
  }

  await reviewDomain(decodeURIComponent(domain), verdict, notes);
  return NextResponse.json({ ok: true });
}
