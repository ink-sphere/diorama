import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "Diorama — Your reading room", description: "A quiet home for your books." };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
