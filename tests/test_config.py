"""Configuration : choix du lien, valeurs par défaut, échec fort si incohérent."""

from __future__ import annotations

import pytest

from bridge.app.config import Config, ConfigError, SerialEndpoint, TcpEndpoint

_BASE = {"PROFILE": "/etc/bridge/profile.yaml", "MQTT_HOST": "mosquitto"}


def _env(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    for name in (
        "MODBUS_TRANSPORT",
        "SERIAL_PORT",
        "MODBUS_HOST",
        "MODBUS_PORT",
        "MODBUS_FRAMING",
        "MODBUS_TIMEOUT",
        "READ_ONLY",
    ):
        monkeypatch.delenv(name, raising=False)
    for key, value in {**_BASE, **extra}.items():
        monkeypatch.setenv(key, value)


def test_serial_is_the_default_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, SERIAL_PORT="/dev/rs485")
    assert Config.from_env().endpoint == SerialEndpoint("/dev/rs485")


def test_tcp_endpoint_defaults_to_502_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, MODBUS_TRANSPORT="tcp", MODBUS_HOST="192.168.1.50")
    endpoint = Config.from_env().endpoint
    assert endpoint == TcpEndpoint(host="192.168.1.50", port=502, framing="socket", timeout_s=3.0)


def test_tcp_gateway_framing_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        MODBUS_TRANSPORT="tcp",
        MODBUS_HOST="gw.lan",
        MODBUS_PORT="8899",
        MODBUS_FRAMING="rtu",
    )
    endpoint = Config.from_env().endpoint
    assert isinstance(endpoint, TcpEndpoint)
    assert (endpoint.port, endpoint.framing) == (8899, "rtu")


def test_read_only_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, SERIAL_PORT="/dev/rs485")
    assert Config.from_env().read_only is True


@pytest.mark.parametrize(
    "extra",
    [
        {},  # transport série sans SERIAL_PORT
        {"MODBUS_TRANSPORT": "tcp"},  # transport TCP sans MODBUS_HOST
        {"MODBUS_TRANSPORT": "tcp", "MODBUS_HOST": "x", "MODBUS_FRAMING": "ascii"},
        {"MODBUS_TRANSPORT": "tcp", "MODBUS_HOST": "x", "MODBUS_PORT": "cinq-cent-deux"},
        {"MODBUS_TRANSPORT": "udp"},  # lien inconnu
    ],
)
def test_invalid_configuration_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, extra: dict[str, str]
) -> None:
    _env(monkeypatch, **extra)
    with pytest.raises(ConfigError):
        Config.from_env()
