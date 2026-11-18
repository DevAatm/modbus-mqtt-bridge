"""Adaptateur YAML -> domain.bus : fichier de déploiement d'un bus partagé.

Décrit QUELS équipements interroger sur un même lien, pas COMMENT les lire
(c'est le profil). Exemple :

    devices:
      - { name: sdm120-pac,    profile: profiles/eastron-sdm120.yaml, slave_id: 1 }
      - { name: sdm120-ballon, profile: profiles/eastron-sdm120.yaml, slave_id: 2 }

`name` et `slave_id` remplacent ceux du profil : un même profil sert plusieurs
équipements. Chemins de profil absolus, ou relatifs au fichier de bus.

ÉCHOUE FORT (cf. CLAUDE.md) : toute incohérence lève `BusError` avant que le
service ne démarre. Un profil référencé invalide est aussi une `BusError`, qui
nomme l'entrée fautive.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from bridge.adapters.profile_yaml import ProfileError, load_profile, strict_int
from bridge.domain.bus import Bus
from bridge.domain.profile import DeviceProfile
from bridge.domain.result import Err
from bridge.domain.values import DeviceId, SlaveId

_FIELDS: frozenset[str] = frozenset({"name", "profile", "slave_id"})


class BusError(Exception):
    """Fichier de bus invalide. Levée à la frontière, avant démarrage du service."""


def load_bus(path: str) -> Bus:
    """Charge et valide un fichier de bus. Lève `BusError` si invalide."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except OSError as exc:
        raise BusError(f"fichier de bus illisible ({path}): {exc}") from exc
    except yaml.YAMLError as exc:
        raise BusError(f"YAML invalide ({path}): {exc}") from exc

    if not isinstance(raw, dict):
        raise BusError(f"{path}: le fichier de bus doit être un mapping YAML au niveau racine")
    entries = raw.get("devices")
    if not isinstance(entries, list) or not entries:
        raise BusError(f"{path}: 'devices' doit être une liste non vide")

    base = Path(path).parent
    cache: dict[Path, DeviceProfile] = {}
    devices = [_parse_entry(entry, i, base, cache) for i, entry in enumerate(entries)]

    bus = Bus.of(devices)
    if isinstance(bus, Err):
        raise BusError(f"{path}: {bus.error}")
    return bus.value


def single_device_bus(profile_path: str) -> Bus:
    """Mode historique (`PROFILE`) : un bus d'un seul équipement, tel que décrit
    par le profil (nom et adresse inchangés). Lève `ProfileError` si invalide.
    """
    profile = load_profile(profile_path)
    bus = Bus.of((profile,))
    if isinstance(bus, Err):
        raise ProfileError(f"device: {bus.error}")
    return bus.value


def _parse_entry(
    raw: Any, index: int, base: Path, cache: dict[Path, DeviceProfile]
) -> DeviceProfile:
    ctx = f"devices[{index}]"
    if not isinstance(raw, dict):
        raise BusError(f"{ctx}: mapping attendu, reçu {type(raw).__name__}")
    # Une faute de frappe (`slave: 2`) ne doit pas laisser l'adresse du profil
    # s'appliquer en silence : clé inconnue = refus.
    unknown = sorted(str(k) for k in raw if k not in _FIELDS)
    if unknown:
        raise BusError(f"{ctx}: champ(s) inconnu(s) {unknown} (attendu {sorted(_FIELDS)})")
    missing = sorted(_FIELDS - raw.keys())
    if missing:
        raise BusError(f"{ctx}: champ(s) requis manquant(s) {missing}")

    name = _name(raw["name"], ctx)
    ctx = f"{ctx} {name!r}"
    slave_id = _slave_id(raw["slave_id"], ctx)
    profile = _profile(raw["profile"], base, ctx, cache)
    return replace(profile, name=name, slave_id=slave_id)


def _name(value: Any, ctx: str) -> str:
    if not isinstance(value, str):
        raise BusError(f"{ctx}: name doit être une chaîne, reçu {value!r}")
    parsed = DeviceId.parse(value)
    if isinstance(parsed, Err):
        raise BusError(f"{ctx}: {parsed.error}")
    return parsed.value.name


def _slave_id(value: Any, ctx: str) -> SlaveId:
    try:
        raw = strict_int(value, "slave_id", ctx)
    except ProfileError as exc:
        raise BusError(str(exc)) from None
    parsed = SlaveId.parse(raw)
    if isinstance(parsed, Err):
        raise BusError(f"{ctx}: {parsed.error}")
    return parsed.value


def _profile(value: Any, base: Path, ctx: str, cache: dict[Path, DeviceProfile]) -> DeviceProfile:
    if not isinstance(value, str) or not value.strip():
        raise BusError(f"{ctx}: profile doit être un chemin, reçu {value!r}")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    # Le même profil sert souvent plusieurs équipements : chargé une fois.
    if path not in cache:
        try:
            cache[path] = load_profile(str(path))
        except ProfileError as exc:
            raise BusError(f"{ctx}: profil invalide: {exc}") from exc
    return cache[path]
