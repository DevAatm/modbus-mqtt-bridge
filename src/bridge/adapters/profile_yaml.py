"""Adaptateur YAML -> domain.profile.

Valide le profil au chargement et ÉCHOUE FORT si invalide (cf. CLAUDE.md) :
un profil douteux ne doit jamais produire un service qui écrit n'importe où.
Toute incohérence lève `ProfileError` avant que le service ne démarre.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta
from typing import Any, cast, get_args

import yaml

from bridge.domain.profile import (
    BlockSpec,
    DeviceProfile,
    LayoutSpec,
    PointSpec,
    RegisterType,
    SerialSettings,
    WordOrder,
    WriteMode,
    WriteSpec,
)
from bridge.domain.result import Err, Ok, Result
from bridge.domain.values import RegisterAddress, SlaveId

_DURATION = re.compile(r"^\s*(\d+)\s*(ms|s|m|h)\s*$")
_UNITS: dict[str, float] = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

_REGISTER_TYPES: frozenset[str] = frozenset(get_args(RegisterType))
_WORD_ORDERS: frozenset[str] = frozenset(get_args(WordOrder))


class ProfileError(Exception):
    """Profil invalide. Levée à la frontière, avant démarrage du service."""


def parse_duration(raw: str) -> Result[timedelta, str]:
    match = _DURATION.match(str(raw))
    if match is None:
        return Err(f"durée invalide: {raw!r} (attendu ex: '5s', '500ms')")
    amount, unit = match.groups()
    return Ok(timedelta(seconds=int(amount) * _UNITS[unit]))


def load_profile(path: str) -> DeviceProfile:
    """Charge et valide un profil. Lève `ProfileError` si invalide."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except OSError as exc:
        raise ProfileError(f"profil illisible ({path}): {exc}") from exc
    except yaml.YAMLError as exc:
        raise ProfileError(f"YAML invalide ({path}): {exc}") from exc

    if not isinstance(raw, dict):
        raise ProfileError("le profil doit être un mapping YAML au niveau racine")

    return _parse_profile(raw)


# --- Parsing interne -------------------------------------------------------


def _require(mapping: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in mapping:
        raise ProfileError(f"{ctx}: champ requis manquant {key!r}")
    return mapping[key]


def _as_mapping(value: Any, ctx: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileError(f"{ctx}: mapping attendu, reçu {type(value).__name__}")
    return value


def _unwrap(result: Result[Any, str], ctx: str) -> Any:
    if isinstance(result, Err):
        raise ProfileError(f"{ctx}: {result.error}")
    return result.value


def _register_type(value: Any, ctx: str) -> RegisterType:
    if value not in _REGISTER_TYPES:
        raise ProfileError(f"{ctx}: type invalide {value!r} (attendu {sorted(_REGISTER_TYPES)})")
    return cast(RegisterType, value)


def _word_order(value: Any, ctx: str) -> WordOrder:
    if value not in _WORD_ORDERS:
        raise ProfileError(f"{ctx}: word_order invalide {value!r}")
    return cast(WordOrder, value)


def _parse_serial(raw: Any, ctx: str) -> SerialSettings:
    if raw is None:
        return SerialSettings()
    m = _as_mapping(raw, ctx)
    defaults = SerialSettings()
    return SerialSettings(
        baudrate=int(m.get("baudrate", defaults.baudrate)),
        bytesize=int(m.get("bytesize", defaults.bytesize)),
        parity=str(m.get("parity", defaults.parity)),
        stopbits=int(m.get("stopbits", defaults.stopbits)),
    )


def _parse_point(raw: Any, block_name: str, count: int) -> PointSpec:
    ctx = f"block {block_name!r} point"
    m = _as_mapping(raw, ctx)
    key = str(_require(m, "key", ctx))
    ctx = f"block {block_name!r} point {key!r}"
    offset = int(_require(m, "offset", ctx))
    type_ = _register_type(_require(m, "type", ctx), ctx)
    word_order = _word_order(m.get("word_order", "big"), ctx)

    width = 2 if type_ in ("u32", "s32") else 1
    if offset < 0 or offset + width > count:
        raise ProfileError(
            f"{ctx}: offset {offset} (+{width}) déborde du bloc de {count} registres"
        )

    return PointSpec(
        key=key,
        offset=offset,
        type=type_,
        scale=float(m.get("scale", 1.0)),
        unit=str(m.get("unit", "")),
        word_order=word_order,
    )


def _parse_block(raw: Any) -> BlockSpec:
    ctx = "block"
    m = _as_mapping(raw, ctx)
    name = str(_require(m, "name", ctx))
    ctx = f"block {name!r}"
    address = _unwrap(RegisterAddress.parse(_require(m, "address", ctx)), ctx)
    count = int(_require(m, "count", ctx))
    if count < 1:
        raise ProfileError(f"{ctx}: count doit être >= 1")
    interval = _unwrap(parse_duration(_require(m, "interval", ctx)), ctx)

    points_raw = _require(m, "points", ctx)
    if not isinstance(points_raw, list) or not points_raw:
        raise ProfileError(f"{ctx}: 'points' doit être une liste non vide")
    points = tuple(_parse_point(p, name, count) for p in points_raw)

    return BlockSpec(name=name, address=address, count=count, interval=interval, points=points)


def _parse_layout(raw: Any, write_key: str, count: int) -> LayoutSpec:
    ctx = f"write {write_key!r} layout"
    m = _as_mapping(raw, ctx)
    offset = int(_require(m, "offset", ctx))
    type_ = _register_type(_require(m, "type", ctx), ctx)
    width = 2 if type_ in ("u32", "s32") else 1
    if offset < 0 or offset + width > count:
        raise ProfileError(
            f"{ctx}: offset {offset} (+{width}) déborde du bloc de {count} registres"
        )
    return LayoutSpec(
        offset=offset,
        type=type_,
        key=str(m.get("key", "")),
        scale=float(m.get("scale", 1.0)),
        min=None if m.get("min") is None else float(m["min"]),
        max=None if m.get("max") is None else float(m["max"]),
        word_order=_word_order(m.get("word_order", "big"), ctx),
    )


def _resolve_layout_keys(
    layout: tuple[LayoutSpec, ...], write_key: str, ctx: str
) -> tuple[LayoutSpec, ...]:
    """Attribue/valide les clés de champ.

    Mono-champ : `key` par défaut = clé d'écriture. Multi-champ : chaque champ
    doit avoir une `key` explicite et unique (elle nomme la valeur JSON).
    """
    if len(layout) == 1:
        field = layout[0]
        return (field if field.key else replace(field, key=write_key),)

    seen: set[str] = set()
    for field in layout:
        if not field.key:
            raise ProfileError(
                f"{ctx}: bloc multi-champ, chaque champ 'layout' doit déclarer 'key'"
            )
        if field.key in seen:
            raise ProfileError(f"{ctx}: clé de champ dupliquée: {field.key!r}")
        seen.add(field.key)
    return layout


def _parse_write(raw: Any) -> WriteSpec:
    ctx = "write"
    m = _as_mapping(raw, ctx)
    key = str(_require(m, "key", ctx))
    ctx = f"write {key!r}"
    address = _unwrap(RegisterAddress.parse(_require(m, "address", ctx)), ctx)

    mode_raw = _require(m, "mode", ctx)
    try:
        mode = WriteMode(mode_raw)
    except ValueError:
        raise ProfileError(f"{ctx}: mode invalide {mode_raw!r} (single|block)") from None

    allowed_raw = m.get("allowed")
    allowed = tuple(int(v) for v in allowed_raw) if allowed_raw is not None else None

    if mode is WriteMode.SINGLE:
        if "type" not in m:
            raise ProfileError(f"{ctx}: mode single exige 'type'")
        if m.get("layout") is not None:
            raise ProfileError(f"{ctx}: mode single n'accepte pas 'layout'")
        return WriteSpec(
            key=key,
            address=address,
            mode=mode,
            type=_register_type(m["type"], ctx),
            scale=float(m.get("scale", 1.0)),
            word_order=_word_order(m.get("word_order", "big"), ctx),
            allowed=allowed,
        )

    # mode block
    count = int(_require(m, "count", ctx))
    if count < 1:
        raise ProfileError(f"{ctx}: count doit être >= 1")
    layout_raw = _require(m, "layout", ctx)
    if not isinstance(layout_raw, list) or not layout_raw:
        raise ProfileError(f"{ctx}: mode block exige un 'layout' non vide")
    if allowed is not None:
        raise ProfileError(f"{ctx}: 'allowed' ne s'applique qu'au mode single")
    layout = tuple(_parse_layout(item, key, count) for item in layout_raw)
    layout = _resolve_layout_keys(layout, key, ctx)
    return WriteSpec(
        key=key,
        address=address,
        mode=mode,
        count=count,
        layout=layout,
        preserve=bool(m.get("preserve", False)),
    )


def _parse_profile(raw: dict[str, Any]) -> DeviceProfile:
    device = _as_mapping(_require(raw, "device", "profil"), "device")
    name = str(_require(device, "name", "device"))
    protocol = str(_require(device, "protocol", "device"))
    slave_id = _unwrap(SlaveId.parse(int(_require(device, "slave_id", "device"))), "device")
    serial = _parse_serial(device.get("serial"), "device.serial")

    blocks_raw = _require(raw, "blocks", "profil")
    if not isinstance(blocks_raw, list) or not blocks_raw:
        raise ProfileError("profil: 'blocks' doit être une liste non vide")
    blocks = tuple(_parse_block(b) for b in blocks_raw)

    writes_raw = raw.get("writes") or []
    if not isinstance(writes_raw, list):
        raise ProfileError("profil: 'writes' doit être une liste")
    writes = tuple(_parse_write(w) for w in writes_raw)

    _check_unique_keys(blocks, writes)

    return DeviceProfile(
        name=name,
        protocol=protocol,
        slave_id=slave_id,
        serial=serial,
        blocks=blocks,
        writes=writes,
    )


def _check_unique_keys(blocks: tuple[BlockSpec, ...], writes: tuple[WriteSpec, ...]) -> None:
    seen: set[str] = set()
    for block in blocks:
        for point in block.points:
            if point.key in seen:
                raise ProfileError(f"clé de point dupliquée: {point.key!r}")
            seen.add(point.key)
    write_keys: set[str] = set()
    for write in writes:
        if write.key in write_keys:
            raise ProfileError(f"clé d'écriture dupliquée: {write.key!r}")
        write_keys.add(write.key)
