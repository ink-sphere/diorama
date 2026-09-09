import { listBooks } from "@/lib/library";
export const dynamic = "force-dynamic";
export async function GET() {
  try { return Response.json(await listBooks(), { headers: { "Cache-Control": "no-store" } }); }
  catch { return Response.json({ error: "The library directory could not be read." }, { status: 500 }); }
}
