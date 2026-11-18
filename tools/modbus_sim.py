"""Simulateur Modbus pour les essais — HORS produit, jamais dans l'image runtime.

Sert le bloc `battery` du profil de référence (0x0604, 7 registres) avec des
valeurs plausibles, et ouvre en écriture la zone des consignes (0x1100..0x11FF)
pour tester storage_mode (0x1110) et power_setpoint (0x1187, bloc 0x10).

Usage :
    python -m tools.modbus_sim serial <port_série>
    python -m tools.modbus_sim tcp <port> [--framing socket|rtu]
    python -m tools.modbus_sim solarman <port> --serial <n° logger>

    Option commune `--slaves 1,2` : sert plusieurs esclaves sur le même lien
    (mêmes registres, mémoire séparée), pour essayer un fichier de bus.
    Défaut : l'esclave 1 seul.

Les framings TCP sont simulables : `socket` imite un équipement Modbus TCP
natif, `rtu` une passerelle transparente TCP<->RS485, `solarman` un logger
Sofar/Solarman. C'est le seul moyen de vérifier sans matériel que le bridge
parle au bon dialecte.

Le mode `solarman` n'est qu'une enveloppe : il désencapsule la requête et la
relaie à un vrai serveur Modbus RTU interne, puis réencapsule la réponse. Il
valide donc l'intégration, PAS le format réel attendu par un logger physique —
l'enveloppe y est écrite d'après la même compréhension du protocole que le
framer du bridge.

NB: API pymodbus 3.15 (SimData/SimDevice). Valeurs statiques — l'objectif est
de valider la chaîne lecture/écriture, pas de rejouer une dynamique batterie.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import struct

from pymodbus.framer import FramerType
from pymodbus.server import StartAsyncSerialServer, StartAsyncTcpServer
from pymodbus.simulator import SimData, SimDevice
from pymodbus.simulator.simdata import DataType

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger("modbus_sim")

DEFAULT_SLAVES = (1,)


def _build_device(slave_id: int) -> SimDevice:
    # Espace d'adressage unifié (registres) : le bridge n'utilise que les
    # fonctions registres (0x03/0x06/0x10). SimData neufs à chaque appel :
    # chaque esclave a sa propre mémoire, une écriture sur l'un ne se voit
    # pas sur l'autre.
    # Bloc battery : voltage 50.0V, courant -1A (0xFF9C), puissance -500W
    # (0xFFCE), temp 25°C, soc 80% + n° d'esclave (distingue les esclaves à la
    # lecture), soh 100%, +1 registre de réserve.
    battery = SimData(
        0x0604,
        values=[500, 0xFF9C, 0xFFCE, 25, 80 + slave_id - 1, 100, 0],
        datatype=DataType.REGISTERS,
    )
    # Zone de consignes, ouverte en écriture (readonly=False par défaut).
    setpoints = SimData(0x1100, count=0x100, values=0, datatype=DataType.REGISTERS)
    return SimDevice(id=slave_id, simdata=[battery, setpoints])


def _build_devices(slaves: tuple[int, ...]) -> list[SimDevice]:
    return [_build_device(slave_id) for slave_id in slaves]


async def serve_serial(port: str, slaves: tuple[int, ...] = DEFAULT_SLAVES) -> None:
    _log.info("simulateur Modbus RTU sur %s (slaves %s)", port, list(slaves))
    await StartAsyncSerialServer(
        _build_devices(slaves),
        framer=FramerType.RTU,
        port=port,
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
    )


async def serve_tcp(port: int, framing: str, slaves: tuple[int, ...] = DEFAULT_SLAVES) -> None:
    framer = FramerType.RTU if framing == "rtu" else FramerType.SOCKET
    _log.info("simulateur Modbus TCP sur :%d framing=%s (slaves %s)", port, framing, list(slaves))
    await StartAsyncTcpServer(
        _build_devices(slaves),
        framer=framer,
        address=("0.0.0.0", port),  # conteneur d'essai uniquement
    )


# --- Enveloppe Solarman V5 (essais seulement) -------------------------------

_V5_START = 0xA5
_V5_END = 0x15
_V5_RESPONSE = 0x1510
_V5_REQUEST_HEADER = 15  # frame_type(1) sensor(2) total(4) power_on(4) offset(4)


def _v5_wrap(modbus_frame: bytes, sequence: int, logger_serial: int) -> bytes:
    body = bytes([2, 1]) + bytes(12) + modbus_frame  # frame_type + status + compteurs
    frame = bytearray(
        struct.pack("<BHHHI", _V5_START, len(body), _V5_RESPONSE, sequence, logger_serial)
    )
    frame += body + bytes(1) + bytes([_V5_END])
    frame[-2] = sum(frame[1:-2]) & 0xFF
    return bytes(frame)


def _v5_unwrap(frame: bytes) -> tuple[bytes, int] | None:
    """Retourne (trame RTU, n° de séquence), ou None si la trame est incomplète."""
    if len(frame) < 13 or frame[0] != _V5_START:
        return None
    body_len = struct.unpack_from("<H", frame, 1)[0]
    if len(frame) < body_len + 13:
        return None
    sequence = struct.unpack_from("<H", frame, 5)[0]
    return frame[11 + _V5_REQUEST_HEADER : 11 + body_len], sequence


async def serve_solarman(
    port: int, logger_serial: int, slaves: tuple[int, ...] = DEFAULT_SLAVES
) -> None:
    """Logger simulé : enveloppe V5 devant un serveur Modbus RTU interne."""
    inner_port = port + 1000
    # Référence gardée : la tâche vit aussi longtemps que le process.
    inner = asyncio.create_task(serve_tcp(inner_port, "rtu", slaves))
    await asyncio.sleep(0.5)
    assert not inner.done(), "serveur Modbus interne arrêté prématurément"

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buffer = b""
        try:
            while data := await reader.read(1024):
                buffer += data
                while (parsed := _v5_unwrap(buffer)) is not None:
                    rtu, sequence = parsed
                    buffer = buffer[struct.unpack_from("<H", buffer, 1)[0] + 13 :]
                    up_r, up_w = await asyncio.open_connection("127.0.0.1", inner_port)
                    up_w.write(rtu)
                    await up_w.drain()
                    response = await asyncio.wait_for(up_r.read(256), timeout=2.0)
                    up_w.close()
                    writer.write(_v5_wrap(response, sequence, logger_serial))
                    await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    _log.info("logger Solarman simulé sur :%d (série %d)", port, logger_serial)
    server = await asyncio.start_server(handle, "0.0.0.0", port)
    async with server:
        await server.serve_forever()


def _slaves(raw: str) -> tuple[int, ...]:
    try:
        slaves = tuple(int(part) for part in raw.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"liste d'entiers attendue: {raw!r}") from None
    if not slaves or any(not 1 <= s <= 247 for s in slaves) or len(set(slaves)) != len(slaves):
        raise argparse.ArgumentTypeError(f"adresses distinctes dans 1..247 attendues: {raw!r}")
    return slaves


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slaves",
        type=_slaves,
        default=DEFAULT_SLAVES,
        help="adresses d'esclave servies, séparées par des virgules (défaut: 1)",
    )
    sub = parser.add_subparsers(dest="link", required=True)
    serial = sub.add_parser("serial", help="serveur RTU sur port série")
    serial.add_argument("port", help="chemin du port série (ou PTY)")
    tcp = sub.add_parser("tcp", help="serveur TCP")
    tcp.add_argument("port", type=int, help="port d'écoute")
    tcp.add_argument("--framing", choices=("socket", "rtu"), default="socket")
    solarman = sub.add_parser("solarman", help="logger Solarman V5 simulé")
    solarman.add_argument("port", type=int, help="port d'écoute (8899 en usage)")
    solarman.add_argument("--serial", type=int, required=True, help="n° de série du logger")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.link == "serial":
        asyncio.run(serve_serial(args.port, args.slaves))
    elif args.link == "solarman":
        asyncio.run(serve_solarman(args.port, args.serial, args.slaves))
    else:
        asyncio.run(serve_tcp(args.port, args.framing, args.slaves))
