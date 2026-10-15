import type { Metadata } from "next";

import { MarketView } from "@/components/market-view";

export const metadata: Metadata = { title: "Market — QuantForge" };

export default function Page() {
  return <MarketView />;
}
