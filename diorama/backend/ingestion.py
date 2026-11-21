"""Add an EPUB to a library through an asynchronous ingestion harness."""

from pathlib import Path

from diorama.agent.geppetto import Geppetto
from diorama.agent.harness import GeppettoHarness, IngestionResult
from diorama.agent.preparation import ProgressListener
from diorama.agent.trace import TraceListener
from diorama.agent.worker import docker_worker


class BookIngestion:
    """Backend adapter for trusted local uploads and a persistent library directory."""

    def __init__(self, harness: GeppettoHarness):
        self.harness = harness

    @classmethod
    def local(cls, data_root: Path = Path(".diorama_data")) -> "BookIngestion":
        """Configure Geppetto's Docker worker and resolve the storage directory.

        Relative paths are resolved against the current working directory.
        The worker starts on a call to `add_book` that needs a new manifest.
        """
        return cls(GeppettoHarness(Geppetto.load(), docker_worker, data_root.resolve()))

    async def add_book(
        self,
        book_id: str,
        uploaded_epub: Path,
        *,
        on_progress: ProgressListener | None = None,
        on_trace: TraceListener | None = None,
    ) -> IngestionResult:
        """Ingest a trusted local EPUB under the caller's stable book ID.

        The result points to the published manifest and retained source edition.
        Keep these paths if the book will be reopened in another process.
        Supply a synchronous `on_progress` listener to receive preparation milestones.
        """
        return await self.harness.ingest_epub(
            book_id=book_id,
            source_epub=uploaded_epub.resolve(),
            on_progress=on_progress,
            on_trace=on_trace,
        )
