import type { Metadata } from "next";

import { PerformanceView } from "@/components/performance-view";

export const metadata: Metadata = { title: "Performance — QuantForge" };

export default function Page() {
  return <PerformanceView />;
}
