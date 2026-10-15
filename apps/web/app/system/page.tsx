import type { Metadata } from "next";

import { SystemView } from "@/components/system-view";

export const metadata: Metadata = { title: "System — QuantForge" };

export default function Page() {
  return <SystemView />;
}
