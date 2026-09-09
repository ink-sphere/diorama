import { bookInfo, LibraryError } from "@/lib/library";
export const dynamic = "force-dynamic";
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  try { return Response.json(await bookInfo((await params).id), { headers: { "Cache-Control": "no-store" } }); }
  catch (error) { return Response.json({ error: error instanceof LibraryError ? error.message : "Cannot read this book" }, { status: error instanceof LibraryError ? error.status : 500 }); }
}
