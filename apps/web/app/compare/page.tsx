import type { Metadata } from "next";

import { CompareView } from "@/components/compare-view";

export const metadata: Metadata = { title: "Compare — QuantForge" };

export default function Page() {
  return <CompareView />;
}
