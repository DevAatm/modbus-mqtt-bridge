"""Adaptateur horloge : asyncio réel. Implémente le port `Clock`."""

from __future__ import annotations

import asyncio
import time


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
