import { Suspense } from "react";
import { Reader } from "@/components/Reader";
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  return <Suspense fallback={<p className="empty">Opening your book…</p>}><Reader bookId={(await params).id} /></Suspense>;
}
