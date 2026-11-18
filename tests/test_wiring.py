"""Wiring : parsing des charges utiles de commande, et choix du lien Modbus."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.framer import FramerRTU, FramerSocket

from bridge.adapters.modbus import ModbusBus, PymodbusTransport
from bridge.app.config import (
    BusFile,
    Config,
    ModbusEndpoint,
    ProfileFile,
    SerialEndpoint,
    TcpEndpoint,
)
from bridge.app.wiring import _load_devices, _open_bus, _parse_command, _transports
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
        devices=ProfileFile("p.yaml"),
        read_only=True,
        health_file="h",
        mqtt_host="mosquitto",
        mqtt_port=1883,
        mqtt_username=None,
        mqtt_password=None,
        mqtt_prefix="modbus",
    )


def _transport(config: Config) -> PymodbusTransport:
    profile = _profile()
    return _open_bus(config, profile.serial).for_slave(profile.slave_id)


async def test_serial_endpoint_builds_a_serial_client() -> None:
    transport = _transport(_config(SerialEndpoint("/dev/rs485")))
    assert isinstance(transport._client, AsyncModbusSerialClient)
    assert transport._slave_id == 3


async def test_native_tcp_uses_the_socket_framer() -> None:
    endpoint = TcpEndpoint(host="192.168.1.50", port=502, framing="socket", timeout_s=3.0)
    transport = _transport(_config(endpoint))
    assert isinstance(transport._client, AsyncModbusTcpClient)
    assert isinstance(transport._client.ctx.framer, FramerSocket)
    assert str(transport) == "tcp 192.168.1.50:502 (socket)"


async def test_transparent_gateway_uses_the_rtu_framer() -> None:
    # Passerelle TCP<->RS485 : trames RTU encapsulées, pas d'en-tête MBAP.
    endpoint = TcpEndpoint(host="gw.lan", port=8899, framing="rtu", timeout_s=1.5)
    transport = _transport(_config(endpoint))
    assert isinstance(transport._client.ctx.framer, FramerRTU)


# --- bus partagé ------------------------------------------------------------
# Plusieurs esclaves sur un même lien : une seule connexion, une vue par adresse.


async def test_bus_views_share_one_client_with_their_own_slave_id() -> None:
    bus = _open_bus(_config(SerialEndpoint("/dev/rs485")), SerialSettings())
    assert isinstance(bus, ModbusBus)
    meter_1 = bus.for_slave(SlaveId(1))
    meter_2 = bus.for_slave(SlaveId(2))
    assert meter_1._client is meter_2._client is bus._client
    assert (meter_1.slave_id, meter_2.slave_id) == (SlaveId(1), SlaveId(2))
    assert str(meter_1) == str(bus) == "série /dev/rs485"


# --- source des équipements -------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


def _views(config: Config) -> list[tuple[str, PymodbusTransport]]:
    devices = _load_devices(config.devices)
    bus = _open_bus(config, devices.devices[0].serial)
    return [(p.name, t) for p, t in _transports(devices, bus)]


async def test_bus_file_gives_one_view_per_meter_on_a_single_client(tmp_path: Path) -> None:
    sdm120 = (ROOT / "profiles" / "eastron-sdm120.yaml").as_posix()
    bus_file = tmp_path / "bus.yaml"
    bus_file.write_text(
        "devices:\n"
        f"  - {{name: sdm120-pac,    profile: '{sdm120}', slave_id: 1}}\n"
        f"  - {{name: sdm120-ballon, profile: '{sdm120}', slave_id: 2}}\n",
        encoding="utf-8",
    )
    config = replace(_config(SerialEndpoint("/dev/rs485")), devices=BusFile(str(bus_file)))

    (pac_name, pac), (ballon_name, ballon) = _views(config)

    assert (pac_name, ballon_name) == ("sdm120-pac", "sdm120-ballon")
    assert (pac.slave_id, ballon.slave_id) == (SlaveId(1), SlaveId(2))
    assert pac._client is ballon._client  # une seule connexion au bus
    assert pac._client.comm_params.baudrate == 2400  # réglages du profil partagé


async def test_profile_mode_is_a_single_device_with_the_profile_identity() -> None:
    sofar = str(ROOT / "profiles" / "sofar-esi-5k-s1.yaml")
    config = replace(_config(SerialEndpoint("/dev/rs485")), devices=ProfileFile(sofar))

    ((name, transport),) = _views(config)

    assert name == "sofar-esi-5k-s1"
    assert transport.slave_id == SlaveId(1)
