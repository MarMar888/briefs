import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { isIndustry } from "@/lib/industries";
import { IndustrySwitcher } from "../industry-switcher";

// Per-tenant chrome: the nav links carry the active industry segment, and the
// top-right switcher flips between markets. Unknown industries 404.
export default async function IndustryLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ industry: string }>;
}) {
  const { industry } = await params;
  if (!isIndustry(industry)) {
    notFound();
  }

  return (
    <>
      <header className="topbar">
        <div className="topbarLeft">
          <Link className="brand" href={`/${industry}`}>
            Briefs
          </Link>
          <nav className="nav">
            <Link href={`/${industry}`}>Leads</Link>
            <Link href={`/${industry}/pending`}>Pending</Link>
            <Link href={`/${industry}/keywords`}>Keywords</Link>
            <Link href={`/${industry}/activity`}>Activity</Link>
          </nav>
        </div>
        <IndustrySwitcher current={industry} />
      </header>
      <main className="shell">{children}</main>
    </>
  );
}
