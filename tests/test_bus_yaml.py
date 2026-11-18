"""Fichier de bus : plusieurs équipements sur un même lien, échec fort sinon."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridge.adapters.bus_yaml import BusError, load_bus, single_device_bus
from bridge.adapters.profile_yaml import ProfileError
from bridge.domain.values import SlaveId

ROOT = Path(__file__).resolve().parents[1]
SDM120 = ROOT / "profiles" / "eastron-sdm120.yaml"
SOFAR = ROOT / "profiles" / "sofar-esi-5k-s1.yaml"


def _profile_text(name: str = "meter", baudrate: int = 9600) -> str:
    return (
        f"device: {{name: {name}, protocol: p, slave_id: 1, serial: {{baudrate: {baudrate}}}}}\n"
        "blocks:\n"
        "  - name: b\n    address: 0x10\n    count: 1\n    interval: 1s\n"
        "    points:\n      - {key: k, offset: 0, type: u16}\n"
    )


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _bus(tmp_path: Path, devices_yaml: str) -> str:
    """Fichier de bus à côté d'un profil `meter.yaml` valide."""
    _write(tmp_path / "meter.yaml", _profile_text())
    return str(_write(tmp_path / "bus.yaml", devices_yaml))


# --- cas nominaux -----------------------------------------------------------


def test_same_profile_serves_two_meters_with_their_own_name_and_address(tmp_path: Path) -> None:
    # Le profil du dépôt sert tel quel aux deux compteurs (chemin absolu).
    bus_file = _write(
        tmp_path / "bus.yaml",
        "devices:\n"
        f"  - {{name: sdm120-pac,    profile: '{SDM120.as_posix()}', slave_id: 1}}\n"
        f"  - {{name: sdm120-ballon, profile: '{SDM120.as_posix()}', slave_id: 2}}\n",
    )
    bus = load_bus(str(bus_file))

    pac, ballon = bus.devices
    assert (pac.name, pac.slave_id) == ("sdm120-pac", SlaveId(1))
    assert (ballon.name, ballon.slave_id) == ("sdm120-ballon", SlaveId(2))
    # Tout le reste vient du profil, identique pour les deux.
    assert pac.blocks == ballon.blocks
    assert pac.serial.baudrate == 2400
    assert pac.protocol == "modbus-eastron-sdm"


def test_relative_profile_path_is_resolved_against_the_bus_file(tmp_path: Path) -> None:
    _write(tmp_path / "profiles" / "meter.yaml", _profile_text())
    bus_file = _write(
        tmp_path / "deploy" / "bus.yaml",
        "devices:\n  - {name: a, profile: ../profiles/meter.yaml, slave_id: 3}\n",
    )
    (device,) = load_bus(str(bus_file)).devices
    assert (device.name, device.slave_id) == ("a", SlaveId(3))


def test_single_device_bus_keeps_the_profile_name_and_address() -> None:
    # Mode PROFILE historique : nom et adresse du profil, inchangés.
    (device,) = single_device_bus(str(SOFAR)).devices
    assert (device.name, device.slave_id) == ("sofar-esi-5k-s1", SlaveId(1))


def test_single_device_bus_rejects_a_name_unusable_as_topic(tmp_path: Path) -> None:
    path = _write(tmp_path / "p.yaml", _profile_text(name="'a/b'"))
    with pytest.raises(ProfileError, match="topic"):
        single_device_bus(str(path))


# --- refus ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("devices_yaml", "match"),
    [
        ("devices: []\n", "liste non vide"),
        ("other: 1\n", "liste non vide"),
        ("devices: {a: 1}\n", "liste non vide"),
        ("- 1\n", "mapping"),
        ("devices: [42]\n", "mapping attendu"),
        # slave_id en double : deux équipements sur la même adresse
        (
            "devices:\n"
            "  - {name: a, profile: meter.yaml, slave_id: 1}\n"
            "  - {name: b, profile: meter.yaml, slave_id: 1}\n",
            "slave_id 1 en double",
        ),
        ("devices:\n  - {name: a, profile: meter.yaml, slave_id: 0}\n", "1..247"),
        ("devices:\n  - {name: a, profile: meter.yaml, slave_id: 248}\n", "1..247"),
        ("devices:\n  - {name: a, profile: meter.yaml, slave_id: '1'}\n", "entier"),
        ("devices:\n  - {name: a, profile: meter.yaml, slave_id: [1]}\n", "entier"),
        # name en double : mêmes topics MQTT
        (
            "devices:\n"
            "  - {name: a, profile: meter.yaml, slave_id: 1}\n"
            "  - {name: a, profile: meter.yaml, slave_id: 2}\n",
            "nom d'équipement en double",
        ),
        ("devices:\n  - {name: 'a/b', profile: meter.yaml, slave_id: 1}\n", "topic"),
        ("devices:\n  - {name: 'a+', profile: meter.yaml, slave_id: 1}\n", "topic"),
        ("devices:\n  - {name: '', profile: meter.yaml, slave_id: 1}\n", "vide"),
        ("devices:\n  - {name: 12, profile: meter.yaml, slave_id: 1}\n", "chaîne"),
        # champs
        ("devices:\n  - {name: a, profile: meter.yaml}\n", r"manquant.*slave_id"),
        ("devices:\n  - {name: a, profile: meter.yaml, slave: 2, slave_id: 1}\n", "inconnu"),
        ("devices:\n  - {name: a, profile: absent.yaml, slave_id: 1}\n", "profil invalide"),
        ("devices:\n  - {name: a, profile: 3, slave_id: 1}\n", "chemin"),
    ],
)
def test_invalid_bus_fails_loudly(tmp_path: Path, devices_yaml: str, match: str) -> None:
    with pytest.raises(BusError, match=match):
        load_bus(_bus(tmp_path, devices_yaml))


def test_different_serial_settings_on_one_line_are_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "slow.yaml", _profile_text(baudrate=2400))
    _write(tmp_path / "fast.yaml", _profile_text(baudrate=9600))
    bus_file = _write(
        tmp_path / "bus.yaml",
        "devices:\n"
        "  - {name: a, profile: slow.yaml, slave_id: 1}\n"
        "  - {name: b, profile: fast.yaml, slave_id: 2}\n",
    )
    with pytest.raises(BusError, match="réglages série incompatibles"):
        load_bus(str(bus_file))


def test_invalid_referenced_profile_names_the_faulty_entry(tmp_path: Path) -> None:
    _write(tmp_path / "broken.yaml", "device: {name: x, protocol: p, slave_id: 1}\n")
    bus_file = _write(
        tmp_path / "bus.yaml",
        "devices:\n  - {name: ballon, profile: broken.yaml, slave_id: 2}\n",
    )
    with pytest.raises(BusError, match=r"devices\[0\] 'ballon': profil invalide.*blocks"):
        load_bus(str(bus_file))


def test_unreadable_bus_file_is_a_bus_error(tmp_path: Path) -> None:
    with pytest.raises(BusError, match="illisible"):
        load_bus(str(tmp_path / "absent.yaml"))
