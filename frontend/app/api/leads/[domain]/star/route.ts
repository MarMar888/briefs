import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { toggleStar } from "@/lib/queries";

export async function PATCH(request: NextRequest, { params }: { params: Promise<{ domain: string }> }) {
  const { domain } = await params;
  const body = await request.json().catch(() => null);
  const starred = body?.starred;

  if (typeof starred !== "boolean") {
    return NextResponse.json({ error: "starred must be a boolean" }, { status: 400 });
  }

  await toggleStar(decodeURIComponent(domain), starred);
  return NextResponse.json({ ok: true });
}
