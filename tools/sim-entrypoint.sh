#!/bin/sh
# Simulateur : crée un port série virtuel exposé en TCP, puis lance le serveur
# Modbus RTU dessus. socat sans 'fork' -> une seule liaison (un seul maître).
set -eu

PTY=/tmp/rs485-sim
socat -d pty,raw,echo=0,link="$PTY" tcp-listen:9000,reuseaddr &

# Attendre que le PTY existe avant de démarrer le serveur.
i=0
while [ ! -e "$PTY" ]; do
  i=$((i + 1))
  [ "$i" -gt 100 ] && echo "PTY $PTY jamais apparu" >&2 && exit 1
  sleep 0.1
done

exec python -m tools.modbus_sim "$PTY"
