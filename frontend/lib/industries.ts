// The markets ("tenants") the dashboard can show. Mirrors the verticals defined in
// the Python pipeline's vertical_profiles.py — each lead row carries an `industry`
// matching one of these slugs. The UI scopes everything to the active slug, picked
// from the URL path (/[industry]/...).

export type Industry = {
  slug: string;
  label: string; // shown in the switcher (with emoji, matches email branding)
  short: string; // compact label
};

export const INDUSTRIES: Industry[] = [
  { slug: "outdoor", label: "⛰️ Outdoor Sports", short: "Outdoor" },
  { slug: "construction", label: "🏗️ Construction", short: "Construction" },
  { slug: "minnesota", label: "🧹 Minnesota", short: "MN" },
];

export const DEFAULT_INDUSTRY = "outdoor";

export const INDUSTRY_SLUGS = INDUSTRIES.map((i) => i.slug);

export function isIndustry(slug: string | undefined | null): slug is string {
  return !!slug && INDUSTRY_SLUGS.includes(slug);
}

export function industryLabel(slug: string): string {
  return INDUSTRIES.find((i) => i.slug === slug)?.label ?? slug;
}
