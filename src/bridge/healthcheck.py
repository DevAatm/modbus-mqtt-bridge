"""Healthcheck sans serveur HTTP : lit l'horodatage écrit par le poller.

Sort 0 si la dernière lecture réussie est récente, 1 sinon. Invoqué par le
`healthcheck` Docker (`python -m bridge.healthcheck`).
"""

from __future__ import annotations

import os
import sys
import time

# Marge : le healthcheck tourne toutes les 60s (cf. compose). On tolère
# quelques cycles de poll manqués avant de déclarer le conteneur malade.
_MAX_AGE_SECONDS = 180.0


def main() -> int:
    path = os.environ.get("HEALTH_FILE", "/tmp/bridge-health")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return 1
    age = time.time() - mtime
    return 0 if age <= _MAX_AGE_SECONDS else 1


if __name__ == "__main__":
    sys.exit(main())
