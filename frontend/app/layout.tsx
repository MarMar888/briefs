import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Briefs",
  description: "Domain lead review"
};

// Minimal shell. The per-tenant header + nav live in app/[industry]/layout.tsx
// so they can be scoped to the active industry.
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
