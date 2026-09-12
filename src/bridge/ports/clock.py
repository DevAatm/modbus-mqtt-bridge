"""Port horloge : abstraite pour rendre poll/watchdog testables sans temps réel."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def monotonic(self) -> float:
        """Secondes monotones, pour mesurer des intervalles."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Attend `seconds`. Doit être annulable (CancelledError)."""
        ...
