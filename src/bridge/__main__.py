"""Point d'entrée : `python -m bridge`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from bridge.app.config import Config
from bridge.app.wiring import run


def _configure_logging() -> None:
    """Configure les logs tôt, avant tout le reste. Niveau via LOG_LEVEL."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    _configure_logging()
    config = Config.from_env()
    # Arrêt propre : le TaskGroup annule les boucles sur Ctrl-C.
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(config))


if __name__ == "__main__":
    main()
