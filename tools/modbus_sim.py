"""Simulateur Modbus RTU pour les essais — HORS produit, jamais dans l'image runtime.

Sert le bloc `battery` du profil de référence (0x0604, 7 registres) avec des
valeurs plausibles, et ouvre en écriture la zone des consignes (0x1100..0x11FF)
pour tester storage_mode (0x1110) et power_setpoint (0x1187, bloc 0x10).

Usage : python -m tools.modbus_sim <port_série>

NB: API pymodbus 3.15 (SimData/SimDevice). Valeurs statiques — l'objectif est
de valider la chaîne lecture/écriture, pas de rejouer une dynamique batterie.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from pymodbus.framer import FramerType
from pymodbus.server import StartAsyncSerialServer
from pymodbus.simulator import SimData, SimDevice
from pymodbus.simulator.simdata import DataType

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger("modbus_sim")

SLAVE_ID = 1

# Bloc battery : voltage 50.0V, courant -1A (0xFF9C), puissance -500W (0xFFCE),
# temp 25°C, soc 80%, soh 100%, +1 registre de réserve.
_BATTERY = SimData(
    0x0604,
    values=[500, 0xFF9C, 0xFFCE, 25, 80, 100, 0],
    datatype=DataType.REGISTERS,
)
# Zone de consignes, ouverte en écriture (readonly=False par défaut).
_SETPOINTS = SimData(0x1100, count=0x100, values=0, datatype=DataType.REGISTERS)


def _build_device() -> SimDevice:
    # Espace d'adressage unifié (registres) : le bridge n'utilise que les
    # fonctions registres (0x03/0x06/0x10).
    return SimDevice(id=SLAVE_ID, simdata=[_BATTERY, _SETPOINTS])


async def main(port: str) -> None:
    _log.info("simulateur Modbus RTU sur %s (slave %d)", port, SLAVE_ID)
    await StartAsyncSerialServer(
        _build_device(),
        framer=FramerType.RTU,
        port=port,
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m tools.modbus_sim <port_série>", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1]))
