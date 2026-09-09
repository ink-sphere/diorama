In diorama/agents/ebook_loader_agent.py , create a [Tau AI](https://github.com/huggingface/tau) agent called the `EbookLoaderAgent`. We pass the path to an epub ebook to the `EbookLoaderAgent` and it parses the nested structure of the epub file into a definite data structure with the content of the book organized in an unmodified manner.

For example, for books like /Users/geekyrakshit/Workspace/inksphere/books/dracula.epub or /Users/geekyrakshit/Workspace/inksphere/books/alice-in-wonderland.epub, the books are organized into sequential list of chapters and the main content in nested inside each chapter. For example, in this case, the data structure would be the following:

```json
{
    "title": "Dracula",
    "author": "Bram Stoker",
    "metadata": {...},
    "content": [
        {
            "title": "Jonathan Harker's Journal",
            "index": "I",
            "type": "Chapter",
            "content": ...,
        },
        {
            "title": "Jonathan Harker's Journal",
            "index": "II",
            "type": "Chapter",
            "content": ...,
        },...
        {
            "title": "Letters-Lucy and Mina",
            "index": "V",
            "type": "Chapter",
            "content": ...,
        }
    ]
}
```

For books like /Users/geekyrakshit/Workspace/inksphere/books/pg1523-images-3.epub, the books are organized into Acts and Scenes. So, in this case data structure would be the following:

```json
{
    "title": "As You Like It",
    "author": "Wiiliam Shakespeare",
    "metadata": {...},
    "content": [
        {
            "title": "Act I",
            "index": "I",
            "type": "Act",
            "sub-sections": [
                {
                    "title": "Orchard near Oliver's House",
                    "index": "I",
                    "type": "Scene",
                    "content": ...,
                },
                {
                    "title": "A Lawn before the Duke's Palace",
                    "index": "II",
                    "type": "Scene",
                    "content": ...,
                },
                {
                    "title": "A Room in the Palace",
                    "index": "III",
                    "type": "Scene",
                    "content": ...,
                }
            ],
        },
        ...
    ]
}
```

Similarly for a book like Mahabharat, the nested structure will be something like Parva → Upaparva → Adhyāya → Śloka.

The job of the `EbookLoaderAgent` is to convert the book into this specific data structure. The default location to save these data structures should be in the ".diorama" directory.