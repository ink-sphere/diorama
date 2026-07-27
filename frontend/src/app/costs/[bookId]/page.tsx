import { BookCostView } from "@/components/costs/BookCostView";

/**
 * `params` is a promise in this version of Next — the id has to be awaited before
 * it can be handed to the client component that fetches the ledger.
 */
export default async function BookCostPage({
  params,
}: PageProps<"/costs/[bookId]">) {
  const { bookId } = await params;
  return <BookCostView bookId={bookId} />;
}
