import { ReaderView } from "@/components/reader/ReaderView";

/**
 * `params` is a promise in this version of Next — the id has to be awaited before
 * it can be handed to the client component that does the reading.
 */
export default async function ReadPage({ params }: PageProps<"/read/[id]">) {
  const { id } = await params;
  return <ReaderView bookId={id} />;
}
