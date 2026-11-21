"""Manage device sign-in for the Docker profile shared with ingestion workers."""

import asyncio
import os
from collections.abc import Callable
from contextlib import suppress

from openai_codex import AsyncCodex, AsyncDeviceCodeLoginHandle, CodexConfig

from diorama.agent.worker import IMAGE, PROFILE


def account_client() -> AsyncCodex:
    return AsyncCodex(
        CodexConfig(
            launch_args_override=(
                "docker",
                "run",
                "--rm",
                "--interactive",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=volume,source={PROFILE},target=/var/lib/geppetto",
                IMAGE,
                "app-server",
                "--listen",
                "stdio://",
            )
        )
    )


class AccountConnection:
    def __init__(
        self,
        on_connected: Callable[[], None],
        client_factory: Callable[[], AsyncCodex] = account_client,
    ):
        self.on_connected = on_connected
        self.client_factory = client_factory
        self.client: AsyncCodex | None = None
        self.attempt: AsyncDeviceCodeLoginHandle | None = None
        self.waiter: asyncio.Task | None = None
        self.message = ""
        self.lock = asyncio.Lock()

    def session(self) -> AsyncCodex:
        if self.client is None:
            self.client = self.client_factory()
        return self.client

    def pending(self) -> dict:
        assert self.attempt is not None
        return {
            "status": "pending",
            "verification_url": self.attempt.verification_url,
            "user_code": self.attempt.user_code,
        }

    async def status(self) -> dict:
        async with self.lock:
            if self.attempt:
                return self.pending()
            client = self.session()
            try:
                account = (await client.account()).account
            except Exception:
                await self.close_client()
                raise
            if account and account.root.type == "chatgpt":
                self.on_connected()
                return {"status": "connected"}
            return {"status": "disconnected", "message": self.message}

    async def connect(self) -> dict:
        async with self.lock:
            if not self.attempt:
                client = self.session()
                account = (await client.account()).account
                if account and account.root.type == "chatgpt":
                    self.on_connected()
                    return {"status": "connected"}
                self.message = ""
                self.attempt = await client.login_chatgpt_device_code()
                self.waiter = asyncio.create_task(self.wait(self.attempt))
            return self.pending()

    async def wait(self, attempt: AsyncDeviceCodeLoginHandle) -> None:
        try:
            async with asyncio.timeout(15 * 60):
                result = await attempt.wait()
            if result.success:
                self.on_connected()
            else:
                self.message = (
                    "Sign-in did not complete. Request a new code to try again."
                )
        except Exception:
            self.message = "The sign-in session ended. Request a new code to try again."
        finally:
            if self.attempt is attempt:
                self.attempt = None

    async def cancel_attempt(self) -> None:
        if self.attempt:
            with suppress(Exception):
                async with asyncio.timeout(5):
                    await self.attempt.cancel()
        if self.waiter:
            self.waiter.cancel()
            with suppress(asyncio.CancelledError):
                await self.waiter
        self.attempt = None
        self.waiter = None

    async def disconnect(self) -> dict:
        async with self.lock:
            await self.cancel_attempt()
            client = self.session()
            await client.logout()
            self.message = ""
            return {"status": "disconnected"}

    async def cancel(self) -> dict:
        async with self.lock:
            await self.cancel_attempt()
            self.message = ""
        return await self.status()

    async def close_client(self) -> None:
        if self.client:
            client, self.client = self.client, None
            await client.close()

    async def close(self) -> None:
        await self.cancel_attempt()
        await self.close_client()
