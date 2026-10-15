import type { Metadata } from "next";

import { ReplayView } from "@/components/replay-view";

export const metadata: Metadata = { title: "Replay — QuantForge" };

export default function Page() {
  return <ReplayView />;
}
