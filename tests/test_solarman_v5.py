"""Framer Solarman V5 : enveloppe propriétaire autour d'une trame Modbus RTU.

Le protocole n'est pas documenté par le constructeur. Ces tests fixent ce que
le service émet et ce qu'il accepte ; ils ne prouvent pas que c'est ce qu'un
vrai logger attend — seul un relevé sur matériel le dira.
"""

from __future__ import annotations

import struct

from pymodbus.framer import FramerRTU
from pymodbus.pdu import DecodePDU

from bridge.adapters.solarman_v5 import SolarmanV5Framer

LOGGER_SERIAL = 1234567890
# Lecture de 7 registres à 0x0604 sur l'esclave 1 (bloc battery du profil).
READ_PAYLOAD = bytes.fromhex("03") + struct.pack(">HH", 0x0604, 7)


def _framer() -> SolarmanV5Framer:
    return SolarmanV5Framer(DecodePDU(False), LOGGER_SERIAL)


def _wrap_response(
    modbus_frame: bytes, *, control: int = 0x1510, serial: int = LOGGER_SERIAL
) -> bytes:
    """Construit une réponse V5 à la main (indépendamment de l'encodeur testé)."""
    body = b"\x02\x01" + b"\x00" * 12 + modbus_frame
    frame = bytearray(struct.pack("<BHHHI", 0xA5, len(body), control, 1, serial))
    frame += body + b"\x00\x15"
    frame[-2] = sum(frame[1:-2]) & 0xFF
    return bytes(frame)


def test_request_is_an_rtu_frame_inside_a_v5_envelope() -> None:
    frame = _framer().encode(READ_PAYLOAD, 1, 0)

    assert frame[0] == 0xA5 and frame[-1] == 0x15
    assert struct.unpack_from("<H", frame, 3)[0] == 0x4510  # control requête
    assert struct.unpack_from("<I", frame, 7)[0] == LOGGER_SERIAL
    # Longueur annoncée = taille de la charge utile (en-tête 15 o + trame RTU).
    body_len = struct.unpack_from("<H", frame, 1)[0]
    assert body_len == len(frame) - 13
    # La trame interne est un ADU RTU complet, CRC compris.
    assert frame[26:-2] == FramerRTU(DecodePDU(False)).encode(READ_PAYLOAD, 1, 0)
    assert frame[-2] == sum(frame[1:-2]) & 0xFF


def test_sequence_number_increments_between_requests() -> None:
    framer = _framer()
    first = struct.unpack_from("<H", framer.encode(READ_PAYLOAD, 1, 0), 5)[0]
    second = struct.unpack_from("<H", framer.encode(READ_PAYLOAD, 1, 0), 5)[0]
    assert second == first + 1


def test_response_is_unwrapped_and_decoded() -> None:
    rtu = FramerRTU(DecodePDU(False)).encode(
        bytes.fromhex("03") + bytes([14]) + struct.pack(">7H", 500, 0xFF9C, 0xFFCE, 25, 80, 100, 0),
        1,
        0,
    )
    used, dev_id, _, pdu = _framer().decode(_wrap_response(rtu))

    assert used == len(_wrap_response(rtu))
    assert dev_id == 1
    assert pdu == rtu[1:-2]  # PDU nue : ni adresse ni CRC


def test_incomplete_frame_asks_for_more_data() -> None:
    frame = _wrap_response(FramerRTU(DecodePDU(False)).encode(b"\x03\x02\x01\xf4", 1, 0))
    assert _framer().decode(frame[:-4]) == (0, 0, 0, b"")


def test_unsolicited_frames_are_consumed_not_decoded() -> None:
    """Le logger émet des relevés périodiques et des battements : à ignorer."""
    heartbeat = _wrap_response(b"", control=0x4710)
    used, _, _, pdu = _framer().decode(heartbeat)
    assert (used, pdu) == (len(heartbeat), b"")


def test_corrupted_checksum_is_rejected() -> None:
    frame = bytearray(_wrap_response(FramerRTU(DecodePDU(False)).encode(b"\x03\x02\x01\xf4", 1, 0)))
    frame[-2] ^= 0xFF
    used, _, _, pdu = _framer().decode(bytes(frame))
    assert (used, pdu) == (len(frame), b"")  # consommée, aucune valeur publiée


def test_leading_garbage_is_skipped() -> None:
    frame = _wrap_response(FramerRTU(DecodePDU(False)).encode(b"\x03\x02\x01\xf4", 1, 0))
    used, _, _, pdu = _framer().decode(b"\xde\xad\xbe\xef" + frame)
    assert (used, pdu) == (4, b"")  # on jette jusqu'au prochain 0xA5
