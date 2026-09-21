"""Simulateur Modbus pour les essais — HORS produit, jamais dans l'image runtime.

Sert le bloc `battery` du profil de référence (0x0604, 7 registres) avec des
valeurs plausibles, et ouvre en écriture la zone des consignes (0x1100..0x11FF)
pour tester storage_mode (0x1110) et power_setpoint (0x1187, bloc 0x10).

Usage :
    python -m tools.modbus_sim serial <port_série>
    python -m tools.modbus_sim tcp <port> [--framing socket|rtu]

Les deux framings TCP sont simulables : `socket` imite un équipement Modbus TCP
natif, `rtu` une passerelle transparente TCP<->RS485. C'est le seul moyen de
vérifier sans matériel que le bridge parle au bon dialecte.

NB: API pymodbus 3.15 (SimData/SimDevice). Valeurs statiques — l'objectif est
de valider la chaîne lecture/écriture, pas de rejouer une dynamique batterie.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from pymodbus.framer import FramerType
from pymodbus.server import StartAsyncSerialServer, StartAsyncTcpServer
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


async def serve_serial(port: str) -> None:
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


async def serve_tcp(port: int, framing: str) -> None:
    framer = FramerType.RTU if framing == "rtu" else FramerType.SOCKET
    _log.info("simulateur Modbus TCP sur :%d framing=%s (slave %d)", port, framing, SLAVE_ID)
    await StartAsyncTcpServer(
        _build_device(),
        framer=framer,
        address=("0.0.0.0", port),  # conteneur d'essai uniquement
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="link", required=True)
    serial = sub.add_parser("serial", help="serveur RTU sur port série")
    serial.add_argument("port", help="chemin du port série (ou PTY)")
    tcp = sub.add_parser("tcp", help="serveur TCP")
    tcp.add_argument("port", type=int, help="port d'écoute")
    tcp.add_argument("--framing", choices=("socket", "rtu"), default="socket")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.link == "serial":
        asyncio.run(serve_serial(args.port))
    else:
        asyncio.run(serve_tcp(args.port, args.framing))
