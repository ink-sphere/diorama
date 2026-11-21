# Diorama's Philosohy

Diorama is an AI-assisted ebook reading application. It is suppossed to solve the fundamental problem of deeply immersing the reader into the world of the story that they are currently consuming irrespective of their reading skill or their capability of being able to vividly construct an image of the world of the story they're reading.

Seasoned readers of fiction are able to vividly hallucinate the events of the story and visualize the events of the story, looks of the characters, environments, etc. simply based on the text they're currently reading. For more novice readers, books typically have used on multi-modal aids such as the following for deeper immersion into the story:
- illustrations of characters, scenes, maps, etc. from the story itself (image modality)
- audiobook/audiostory format (audio modality)
- additional structured text such as an index of characters appearing in the story, an index of terminologies appearning in the story, summaries or story recaps in a certain interval, etc.

Diorama is meant to be a modern ebook reader for the AI-era ebook reading experience focussed on deeper immersion.

## Immersion layer 1: Automatic ingestion of Ebooks

Ebooks are gonna be presented as Epubs. Geppetto is expected to automatically parse the content of the book and arrange it into a hierarchical structure roughly represented by the schema in `diorama/schemas/book.py` (feel free to refine the schema). Every book will be organized into this hierarchical structure and Geppetto is suppossed to infer how this structure is gonna look like. I can envision the following kind of hierarchies:

1. Chapters -> Content: For a book like Dracula, it is simple, there are a bunch of chapters which all have their own content. 
2. Self-contained chapters: An anthology of short stories, where each chapter is a self-contained story of their own.
3. Plays or Novels: Plays like "As You Like It" where there are Acts, which have Scenes nested under them and the Scenes themseves contain the content. A lot of novels too have similar kind of hierarchies.
4. Then comes more esoteric texts like Ramayan (Kāṇḍa (book) → Sarga (chapter or canto) → Śloka (verse)) or Mahabharat (Parva (book) → Upaparva (sub-book) → Adhyāya (chapter) → Śloka (verse)).

The ingestion should not modify any core text of the book, just organize and arrange them so that they appear accordingly in the app. Geppetto's first task is to ingest into this format.

## Immersion layer 2: A Dynamic Reading Interface

Diorama's reading interface is supposed to reflect the content of the book that the reader is reading in the form of generative UI. This is the 1st layer of immersion that the reader deals with. When the app is launched, the first interface that the reader encounters is a library/bookshelf interface, to which the user can add books. Once a book is added to Diorama, the main agent named "Geppetto" takes over the book. Geppetto's first task is to read the story and create a reading interface that matches the exact flavor of the story. This reading interface is launched when the ingested book is opened by the reader from the bookshelf.