"""Doubles de test implémentant les ports. Aucun matériel, aucun broker."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator

from bridge.domain.result import Err, Ok, Result
from bridge.domain.values import RegisterAddress
from bridge.ports.publisher import IncomingCommand
from bridge.ports.transport import ModbusError


class FakeTransport:
    """Transport scriptable : réponses, exceptions Modbus, timeouts.

    Journalise l'ordre des opérations (`ops`) pour vérifier la sérialisation
    lecture/écriture.
    """

    def __init__(self) -> None:
        self._reads: deque[Result[tuple[int, ...], ModbusError]] = deque()
        self.writes: list[tuple[RegisterAddress, tuple[int, ...]]] = []
        self.ops: list[str] = []

    def script_read(self, registers: tuple[int, ...]) -> None:
        self._reads.append(Ok(registers))

    def script_read_error(self, error: ModbusError) -> None:
        self._reads.append(Err(error))

    async def read_holding(
        self, address: RegisterAddress, count: int
    ) -> Result[tuple[int, ...], ModbusError]:
        self.ops.append("read")
        if self._reads:
            return self._reads.popleft()
        return Ok(tuple(0 for _ in range(count)))

    async def write_single(self, address: RegisterAddress, value: int) -> Result[None, ModbusError]:
        self.ops.append("write")
        self.writes.append((address, (value,)))
        return Ok(None)

    async def write_block(
        self, address: RegisterAddress, values: tuple[int, ...]
    ) -> Result[None, ModbusError]:
        self.ops.append("write")
        self.writes.append((address, values))
        return Ok(None)


class FakePublisher:
    """Capture les publications et sert un flux de commandes scripté."""

    def __init__(self, commands: list[IncomingCommand] | None = None) -> None:
        self.states: list[tuple[str, float, str]] = []
        self.availability: list[bool] = []
        self.results: list[tuple[str, str]] = []
        self._commands = commands or []

    async def publish_state(self, key: str, value: float, unit: str) -> None:
        self.states.append((key, value, unit))

    async def publish_availability(self, online: bool) -> None:
        self.availability.append(online)

    async def publish_command_result(self, key: str, result: str) -> None:
        self.results.append((key, result))

    async def commands(self) -> AsyncIterator[IncomingCommand]:
        for command in self._commands:
            yield command


class FakeClock:
    """Horloge déterministe : le temps n'avance que sur `sleep`."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
