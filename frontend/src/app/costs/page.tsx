import type { Metadata } from "next";

import { CostsView } from "@/components/costs/CostsView";

export const metadata: Metadata = {
  title: "Costs · Diorama",
  description:
    "What every model call cost, by book, agent, model and upstream provider.",
};

export default function CostsPage() {
  return <CostsView />;
}
