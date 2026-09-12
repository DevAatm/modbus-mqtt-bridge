"""Port publication/souscription MQTT. L'adaptateur `mqtt` l'implémente."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class IncomingCommand:
    """Message reçu sur un topic de commande."""

    key: str
    payload: str


@runtime_checkable
class MessagePublisher(Protocol):
    async def publish_state(self, key: str, value: float, unit: str) -> None:
        """Publie une valeur d'état (retain)."""
        ...

    async def publish_availability(self, online: bool) -> None:
        """Publie online/offline (le offline est aussi le LWT)."""
        ...

    async def publish_command_result(self, key: str, result: str) -> None:
        """Acquitte une commande : accepté / rejeté+motif / échoué+exception."""
        ...

    def commands(self) -> AsyncIterator[IncomingCommand]:
        """Flux des demandes d'écriture reçues."""
        ...
