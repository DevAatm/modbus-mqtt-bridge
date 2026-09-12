"""Encodage : grandeur SI -> registres bruts. Fonctions pures.

Inverse strict de `decoding`. Utilisé par le dispatcher pour préparer une
écriture. Refuse (Result Err) toute valeur qui ne tient pas dans le type
plutôt que de tronquer silencieusement.
"""

from __future__ import annotations

from bridge.domain.decoding import register_width
from bridge.domain.profile import RegisterType, WordOrder
from bridge.domain.result import Err, Ok, Result

_RANGES: dict[RegisterType, tuple[int, int]] = {
    "u16": (0, 0xFFFF),
    "s16": (-0x8000, 0x7FFF),
    "u32": (0, 0xFFFFFFFF),
    "s32": (-0x80000000, 0x7FFFFFFF),
}


def encode_value(
    value: float,
    type_: RegisterType,
    scale: float,
    word_order: WordOrder,
) -> Result[tuple[int, ...], str]:
    """Convertit une grandeur SI en registres 16 bits.

    L'échelle est celle du profil : `raw = value / scale`, arrondi au plus
    proche entier (le décodage fait `raw * scale`).
    """
    if scale == 0:
        return Err("échelle nulle: encodage impossible")

    raw = round(value / scale)
    lo, hi = _RANGES[type_]
    if not lo <= raw <= hi:
        return Err(f"valeur {value} -> {raw} hors plage {type_} [{lo}, {hi}]")

    width = register_width(type_)
    unsigned = raw + (1 << (16 * width)) if raw < 0 else raw

    registers = tuple((unsigned >> (16 * (width - 1 - i))) & 0xFFFF for i in range(width))
    if word_order == "little":
        registers = tuple(reversed(registers))
    return Ok(registers)
