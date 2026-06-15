import { redirect } from "next/navigation";
import { DEFAULT_INDUSTRY } from "@/lib/industries";

// The app is multi-tenant by industry; "/" lands on the default market.
export default function RootPage() {
  redirect(`/${DEFAULT_INDUSTRY}`);
}
