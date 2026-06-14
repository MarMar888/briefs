"use client";

import { type ChangeEvent } from "react";
import { usePathname, useRouter } from "next/navigation";
import { INDUSTRIES } from "@/lib/industries";

// Top-right "organization" switcher. Switching tenants preserves the current
// sub-path (e.g. /outdoor/activity → /construction/activity) so you stay on the
// same view in the other market.
export function IndustrySwitcher({ current }: { current: string }) {
  const pathname = usePathname();
  const router = useRouter();

  function onChange(event: ChangeEvent<HTMLSelectElement>) {
    const next = event.target.value;
    if (next === current) return;
    const rest = (pathname ?? "").split("/").slice(2).join("/");
    router.push(`/${next}${rest ? `/${rest}` : ""}`);
  }

  return (
    <select
      className="industrySwitcher"
      value={current}
      onChange={onChange}
      aria-label="Switch industry"
    >
      {INDUSTRIES.map((industry) => (
        <option key={industry.slug} value={industry.slug}>
          {industry.label}
        </option>
      ))}
    </select>
  );
}
