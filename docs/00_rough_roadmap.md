# Initial Rough Roadmap from Soumik

## Vision behind Diorama

The essential purpose of Diorama is simulate the manner in which humans consume fiction and vividly hallucinate the story like a movie inside their head. Diorama is meant to produce consistent illustrations corresponding to all the scenes in the book that will help anyone reading the story imagine the story in a more effortless manner. Think of Diorama as a massively skilled director who has gone through the entire story, has cast actors to play characters (synthetic characters generated using image generation models) and is now illustratting each scene starring those actors. Now, over the entire duration of the story, these characters might look different (for example ageing, disfigurement, change in clothes/hairstyle, and any other way they change). Basically, Diorama is meant to accurately simulate the entire world of the story as the story progresses, and act as a world model for the story.

## Rough Idea for Solution

Here's a rough idea of how I'm thinking of achieving this:

1. First, a `LiteraryResearchAgent` independently reads through the entire story and researches the web for the literary background of the story/novel. This includes the following type of information:
    1. a complete literary profile of the author:
        1. author name
        2. date of birth and date of death (if applicable)
        3. a generic description of the author and their overall body of works
        4. a description stating the author and their work on the specific story
    2. a complete moodboard of the story:
        1. the different time periods in the story
        2. a profile of different places (fictional and real) in the story
        3. a profile of all the different settings present in the story, including the wardrobe
        4. accordingly determine what should be the ideal mood and artstyle of the illustrations in the story.

        I actually don't know how this moodboard is suppossed to look like, but this moodboard should influence the downstream decisions made by the `CastingDirectorAgent`, the `ArtDirectorAgent`, the `MakeupArtistAgent`, and the `WorldModelDirectorAgent` on producing the actual illustrations. Would really like to get your opinions of this.

2. Next, a `CastingDirectorAgent` independently reads through the entire story and maps each character to the list of cast. This casting profile for each character consists of the following information:
    1. the full name of the character
    2. the aliases of the character: alternate names/aliases using which the character might have been referred in the story
        - note that pronouns don't count as aliases
    3. a description of how the cast for the character looks, a detailed description of how the character looks, that would be used as a prompt for a text-to-image generation model to generate a synthetic actor who plays the character in the illustrations. This should consist of
        1. a generic detailed description of the character
        2. a t2i prompt for a close up shot of the face of the character
        3. a (t+i)2i prompt of the full-body image of the character.
            - for generating the full body, we will pass the face close up of the character along with the prompt to generate the full-body image of the actor.

3. Next, a `ArtDirectorAgent`, the `MakeupArtistAgent`, and the `WorldModelDirectorAgent` takes over and works with the image generation model (a text+images -> image model that is essentially used as a tool) to actually generate the illustration. Now, the purpose of each agent in this stage is clearly cut out:
    - The `ArtDirectorAgent` is a subagent that is responsible for the art direction of the scene, i.e,
        - how the set is suppossed to look/change including the time of day, the lighting, etc.
        - how the different objects/props in the scene are suppossed to look/change
        - how the wardrobe of the agents is suppossed to look/change
    - The `MakeupArtistAgent` is concerned entirely with how the respective actors are suppossed to look in a scene corresponding to each characters present in the scene:
        - for example what the hairstyle, wardrobe, facial expression, etc should be for each character present in the scene
    - The `WorldModelDirectorAgent` is responsible for the following major tasks:
        1. maintaining and updating a state of the world as the reader is reading it
            - the state of the world should not be affected by sudden changes of setting, timeline, flashbacks, etc
            - the state of the world is what dictates the actions of the `ArtDirectorAgent` and the `MakeupArtistAgent`
            - the state of the world also keeps track of minor things in the scene, for example, the positions of each character, their wardbrobe, changing expressions, emotions, positions of the props/objects in the scene
        2. based on the state of the state of the world (world model), it delegates the `ArtDirectorAgent` to render different props, different scenes in the environment, wardrobe of each character
        3. next, it delegates the `MakeupArtistAgent` to update the renderings of the actors based on the wardrobe, expressions, etc.
        4. Finally, the `WorldModelDirectorAgent` uses the image generation tool to render and iteratively refine the scene to make it more accurate as per the world model.

# V0 response from Fable

*The feedback below responds to four decisions taken during the brainstorm: illustrate **every scene** (not a key-scene subset); treat the world model as a **product surface**, not just internal plumbing; handle spoilers on a **best-effort** basis for now; and let **Diorama choose the art style**, but keep it **swappable later**.*

Those answers sharpen the picture considerably. Here's what they imply, plus the honest tensions I see — still no implementation plan, just the consequences of your choices.

**"Every scene" + "world model as product surface" reinforce each other.** If you're paying to derive per-scene state anyway, exposing it (cast pages with the actual reference portraits, a story timeline, "on stage" in the reader margin) amortizes the cost across two features instead of one. It also means the state artifacts must be designed as *data someone will look at*, not just prompt fodder — which happens to be exactly the discipline that keeps them honest. A world state nobody can inspect rots quietly; one rendered on a character page gets its errors noticed.

**"Every scene" makes the looks-library approach non-negotiable rather than just preferable.** At 500+ plates, per-scene creative mutation of a single canonical image will visibly drift — readers will notice the protagonist's face slowly morphing across chapters. Selecting from a fixed library of pre-generated (character × look) references, conditioned fresh each time from the same anchors, is what keeps plate 480 consistent with plate 3. It also keeps per-scene marginal cost down to roughly one state-update pass, one prompt compilation, and one or two image calls.

**On spoilers — one caveat about "best effort later."** Deferring the *rendering policy* is fine (v1 plates may occasionally know too much; acceptable). But I'd urge you not to defer the *data model* part: the casting pass should record, from day one, when each character identity becomes reader-known and which looks are disguises — it's a couple of fields, and the casting agent has already done the reading to know the answer. Retrofitting the reveal-timeline onto already-processed books later means re-running the most expensive analysis pass on your whole shelf. Capture the facts now, enforce them later.

**"Swappable style" has one clean architectural consequence:** the world state must be *style-free*. Everything the state layer records — looks, locations, props, story time — should be described in world terms ("threadbare grey wool coat, patched at the elbows"), never in rendering terms ("soft watercolor wash"). The style bible is a separate artifact applied only at the final compilation/render step. If that separation holds, a re-render in a new style is "regenerate style plates + character look references in the new idiom, then re-run only the render stage over the existing states" — the expensive text-analysis layer never repeats. If it doesn't hold, swappable style is a rewrite.

**The refined shape of your pipeline, restated in your own metaphor:** pre-production reads the book once and produces three durable artifacts — the *style bible* (research agent, with the author profile as a UI garnish rather than a dependency), the *cast book* (characters × looks placed on the story timeline, with reveal/disguise metadata), and the *location registry*. Production then runs linearly through scenes: one continuity pass emits a validated world-state diff per scene, one compilation step assembles state + bible + look references into a render request, and the render conditions on those fixed references (plus the previous plate when contiguous), with at most one critique-and-retry. The director, art director, and makeup artist survive as *stages and prompt sections*, not as a per-scene committee — that's the main revision I'd make to the roadmap, and everything else in your document survives contact with it.

Two things I'd still want your take on when you next pick this up: **which image model you're targeting** (the whole casting mechanism leans on strong multi-reference text+image→image editing — Gemini's image model, GPT-image, and Flux Kontext have meaningfully different strengths on identity preservation and reference count, and that choice will shape how the look library is used), and **whether the reader ever gets a veto** — a "recast this character" button is cheap insurance against the model casting someone wrong for a book a reader knows intimately, and it fits your existing retry-not-perfection philosophy.
