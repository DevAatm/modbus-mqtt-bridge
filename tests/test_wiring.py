"""Wiring : parsing des charges utiles de commande, et choix du lien Modbus."""

from __future__ import annotations

from datetime import timedelta

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.framer import FramerRTU, FramerSocket

from bridge.app.config import Config, ModbusEndpoint, SerialEndpoint, TcpEndpoint
from bridge.app.wiring import _make_transport, _parse_command
from bridge.domain.profile import BlockSpec, DeviceProfile, PointSpec, SerialSettings
from bridge.domain.values import RegisterAddress, SlaveId


def test_scalar_payload() -> None:
    cmd = _parse_command("power_setpoint", " -2000 ")
    assert cmd is not None
    assert cmd.scalar == -2000
    assert cmd.fields is None


def test_json_object_payload() -> None:
    cmd = _parse_command("power_control", '{"active": -2000, "reactive": 0}')
    assert cmd is not None
    assert cmd.scalar is None
    assert cmd.fields == {"active": -2000.0, "reactive": 0.0}


def test_invalid_payload_returns_none() -> None:
    assert _parse_command("k", "abc") is None
    assert _parse_command("k", "[1, 2]") is None  # tableau JSON non supporté
    assert _parse_command("k", "{}") is None  # objet vide
    assert _parse_command("k", '{"a": "x"}') is None  # valeur non numérique


# --- choix du lien ---------------------------------------------------------
# Le même profil doit servir les deux liens : un équipement câblé en RS485 et
# le même modèle atteint par le réseau ne diffèrent que par le déploiement.


def _profile() -> DeviceProfile:
    return DeviceProfile(
        name="dev",
        protocol="p",
        slave_id=SlaveId(3),
        serial=SerialSettings(baudrate=19200),
        blocks=(
            BlockSpec(
                name="b",
                address=RegisterAddress(0x0604),
                count=1,
                interval=timedelta(seconds=5),
                points=(PointSpec("v", 0, "u16"),),
            ),
        ),
        writes=(),
    )


def _config(endpoint: ModbusEndpoint) -> Config:
    return Config(
        endpoint=endpoint,
        profile_path="p.yaml",
        read_only=True,
        health_file="h",
        mqtt_host="mosquitto",
        mqtt_port=1883,
        mqtt_username=None,
        mqtt_password=None,
        mqtt_prefix="modbus",
    )


async def test_serial_endpoint_builds_a_serial_client() -> None:
    transport = _make_transport(_config(SerialEndpoint("/dev/rs485")), _profile())
    assert isinstance(transport._client, AsyncModbusSerialClient)
    assert transport._slave_id == 3


async def test_native_tcp_uses_the_socket_framer() -> None:
    endpoint = TcpEndpoint(host="192.168.1.50", port=502, framing="socket", timeout_s=3.0)
    transport = _make_transport(_config(endpoint), _profile())
    assert isinstance(transport._client, AsyncModbusTcpClient)
    assert isinstance(transport._client.ctx.framer, FramerSocket)
    assert str(transport) == "tcp 192.168.1.50:502 (socket)"


async def test_transparent_gateway_uses_the_rtu_framer() -> None:
    # Passerelle TCP<->RS485 : trames RTU encapsulées, pas d'en-tête MBAP.
    endpoint = TcpEndpoint(host="gw.lan", port=8899, framing="rtu", timeout_s=1.5)
    transport = _make_transport(_config(endpoint), _profile())
    assert isinstance(transport._client.ctx.framer, FramerRTU)
