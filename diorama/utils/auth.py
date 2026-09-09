import asyncio

from tau_coding.credentials import FileCredentialStore
from tau_coding.oauth import login_openai_codex


async def login():
    credential = await login_openai_codex(
        on_auth=lambda info: print(f"Open this URL:\n{info.url}"),
        on_prompt=lambda prompt: asyncio.to_thread(input, f"{prompt.message} "),
        on_progress=print,
        open_browser=True,
        originator="diorama",
    )

    FileCredentialStore().set_oauth("openai-codex", credential)
