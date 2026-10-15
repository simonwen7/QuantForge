import type { Metadata } from "next";

import { LabView } from "@/components/lab-view";

export const metadata: Metadata = { title: "Lab — QuantForge" };

export default function Page() {
  return <LabView />;
}
