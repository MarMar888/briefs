import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Briefs",
  description: "Outdoor sports domain lead review"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link className="brand" href="/">
            Briefs
          </Link>
          <nav className="nav">
            <Link href="/">Leads</Link>
            <Link href="/pending">Pending</Link>
            <Link href="/activity">Activity</Link>
          </nav>
        </header>
        <main className="shell">{children}</main>
      </body>
    </html>
  );
}
