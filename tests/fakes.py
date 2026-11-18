"""Doubles de test implémentant les ports. Aucun matériel, aucun broker."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator

from bridge.domain.profile import RegisterTable
from bridge.domain.result import Err, Ok, Result
from bridge.domain.values import RegisterAddress
from bridge.ports.publisher import IncomingCommand
from bridge.ports.transport import ModbusError


class FakeLine:
    """Ligne physique simulée, partageable entre plusieurs `FakeTransport`.

    Détecte les accès concurrents : chaque trame « entre » sur la ligne, cède
    la main à la boucle (une vraie trame prend du temps), puis « sort ». Deux
    trames en vol en même temps = deux maîtres sur un bus half-duplex.
    """

    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.log: list[tuple[str, str]] = []  # (nom du transport, opération)

    async def transmit(self, who: str, op: str) -> None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.log.append((who, op))
        try:
            await asyncio.sleep(0)
        finally:
            self.in_flight -= 1


class FakeTransport:
    """Transport scriptable : réponses, exceptions Modbus, timeouts.

    Journalise l'ordre des opérations (`ops`) pour vérifier la sérialisation
    lecture/écriture. Plusieurs transports peuvent partager une `FakeLine`
    pour simuler plusieurs esclaves sur un même bus.
    """

    def __init__(self, line: FakeLine | None = None, name: str = "dev") -> None:
        self.line = line if line is not None else FakeLine()
        self.name = name
        self._reads: deque[Result[tuple[int, ...], ModbusError]] = deque()
        self.writes: list[tuple[RegisterAddress, tuple[int, ...]]] = []
        self.ops: list[str] = []
        self.read_tables: list[RegisterTable] = []

    def script_read(self, registers: tuple[int, ...]) -> None:
        self._reads.append(Ok(registers))

    def script_read_error(self, error: ModbusError) -> None:
        self._reads.append(Err(error))

    async def read_registers(
        self, table: RegisterTable, address: RegisterAddress, count: int
    ) -> Result[tuple[int, ...], ModbusError]:
        self.ops.append("read")
        self.read_tables.append(table)
        await self.line.transmit(self.name, "read")
        if self._reads:
            return self._reads.popleft()
        return Ok(tuple(0 for _ in range(count)))

    async def write_single(self, address: RegisterAddress, value: int) -> Result[None, ModbusError]:
        self.ops.append("write")
        self.writes.append((address, (value,)))
        await self.line.transmit(self.name, "write_single")
        return Ok(None)

    async def write_block(
        self, address: RegisterAddress, values: tuple[int, ...]
    ) -> Result[None, ModbusError]:
        self.ops.append("write")
        self.writes.append((address, values))
        await self.line.transmit(self.name, "write_block")
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
