import type { Metadata } from "next";

import { SettingsView } from "@/components/settings/SettingsView";

export const metadata: Metadata = {
  title: "Settings · Diorama",
  description: "Choose the model and key each of Diorama's agents runs with.",
};

export default function SettingsPage() {
  return <SettingsView />;
}
