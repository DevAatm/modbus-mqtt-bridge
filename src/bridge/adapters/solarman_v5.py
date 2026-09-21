"""Framer Solarman V5 : trames Modbus RTU encapsulées, dialecte des loggers
Sofar/Solarman (LSW-3, LSE-3) exposés sur le port 8899.

Ce n'est PAS du Modbus TCP : le logger n'a ni en-tête MBAP ni port 502. Il
attend un datagramme propriétaire préfixé de son propre numéro de série, dans
lequel voyage une trame RTU complète (CRC compris). D'où l'héritage de
`FramerRTU` : seule l'enveloppe est ajoutée.

Trame émise (requête, control 0x4510) ::

    A5 | len(u16 LE) | control(u16 LE) | seq(u16 LE) | logger(u32 LE)
       | 02 | sensor(2) | total(4) | power_on(4) | offset(4) | <trame RTU>
       | checksum | 15

Trame reçue (réponse, control 0x1510) : même enveloppe, mais l'en-tête de
charge utile fait 14 octets (`status` remplace `sensor`), pas 15.

`checksum` = somme des octets entre le marqueur de début et lui-même, modulo
256. Le logger émet aussi des trames non sollicitées (relevés périodiques,
battements) : elles portent un autre `control` et sont ignorées.

Protocole non documenté par le constructeur, décrit par rétro-ingénierie. Les
valeurs publiées doivent être confrontées à l'affichage de l'onduleur avant
d'être considérées comme justes (cf. CLAUDE.md).
"""

from __future__ import annotations

import logging
import struct

from pymodbus.framer import FramerRTU
from pymodbus.pdu import DecodePDU

_log = logging.getLogger(__name__)

_START = 0xA5
_END = 0x15
_CONTROL_REQUEST = 0x4510
_CONTROL_RESPONSE = 0x1510

# start(1) + len(2) + control(2) + seq(2) + logger(4) + checksum(1) + end(1)
_ENVELOPE_SIZE = 13
_PAYLOAD_OFFSET = 11
# requête : frame_type(1) sensor(2) total(4) power_on(4) offset(4)
_REQUEST_HEADER = b"\x02" + b"\x00" * 14
# réponse : frame_type(1) status(1) total(4) power_on(4) offset(4)
_RESPONSE_HEADER_SIZE = 14


class SolarmanV5Framer(FramerRTU):
    """Enveloppe Solarman V5 autour du framer RTU de pymodbus.

    `MIN_SIZE` reste celui de RTU (4) : il sert au décodage de la trame
    INTERNE. La taille de l'enveloppe est vérifiée séparément.
    """

    def __init__(self, decoder: DecodePDU, logger_serial: int) -> None:
        super().__init__(decoder)
        self._logger_serial = logger_serial
        self._sequence = 0

    # --- émission ---
    def encode(self, payload: bytes, device_id: int, tid: int) -> bytes:
        rtu = super().encode(payload, device_id, tid)
        self._sequence = (self._sequence + 1) & 0xFFFF
        body = _REQUEST_HEADER + rtu
        frame = bytearray(
            struct.pack(
                "<BHHHI",
                _START,
                len(body),
                _CONTROL_REQUEST,
                self._sequence,
                self._logger_serial,
            )
        )
        frame += body
        frame += b"\x00"  # emplacement du checksum
        frame += bytes([_END])
        frame[-2] = _checksum(frame)
        return bytes(frame)

    # --- réception ---
    def decode(self, data: bytes) -> tuple[int, int, int, bytes]:
        """Retire l'enveloppe, puis délègue la trame RTU à `FramerRTU`.

        Retourne `(0, ...)` pour demander plus d'octets, ou `(n, ..., b"")`
        pour consommer n octets sans PDU (garbage, trame non sollicitée).
        """
        if len(data) < _ENVELOPE_SIZE:
            return 0, 0, 0, self.EMPTY
        if data[0] != _START:
            # Resynchronisation : on jette jusqu'au prochain marqueur de début.
            nxt = data.find(bytes([_START]), 1)
            skip = len(data) if nxt < 0 else nxt
            _log.debug("solarman: %d octet(s) hors trame ignoré(s)", skip)
            return skip, 0, 0, self.EMPTY

        body_len = struct.unpack_from("<H", data, 1)[0]
        total = body_len + _ENVELOPE_SIZE
        if len(data) < total:
            return 0, 0, 0, self.EMPTY
        if data[total - 1] != _END:
            _log.debug("solarman: marqueur de fin absent, resynchronisation")
            return 1, 0, 0, self.EMPTY
        if (expected := _checksum(data[:total])) != data[total - 2]:
            _log.warning(
                "solarman: checksum invalide (attendu 0x%02x, reçu 0x%02x)",
                expected,
                data[total - 2],
            )
            return total, 0, 0, self.EMPTY

        control = struct.unpack_from("<H", data, 3)[0]
        if control != _CONTROL_RESPONSE:
            # Relevé périodique ou battement du logger : pas notre réponse.
            _log.debug("solarman: trame non sollicitée ignorée (control 0x%04x)", control)
            return total, 0, 0, self.EMPTY

        serial = struct.unpack_from("<I", data, 7)[0]
        if serial != self._logger_serial:
            _log.warning(
                "solarman: numéro de série inattendu (%d, configuré %d)",
                serial,
                self._logger_serial,
            )

        body = data[_PAYLOAD_OFFSET : _PAYLOAD_OFFSET + body_len]
        if len(body) <= _RESPONSE_HEADER_SIZE:
            _log.warning("solarman: réponse sans trame Modbus (logger silencieux ?)")
            return total, 0, 0, self.EMPTY

        _, dev_id, _, pdu = super().decode(body[_RESPONSE_HEADER_SIZE:])
        if not pdu:
            _log.warning("solarman: trame RTU interne illisible (CRC ?)")
            return total, 0, 0, self.EMPTY
        return total, dev_id, 0, pdu


def _checksum(frame: bytes | bytearray) -> int:
    """Somme des octets entre le marqueur de début et le checksum, modulo 256."""
    return sum(frame[1:-2]) & 0xFF
