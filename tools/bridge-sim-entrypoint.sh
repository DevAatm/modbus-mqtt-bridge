#!/bin/sh
# Côté bridge : crée un PTY local tunnelé vers le simulateur (TCP), puis lance
# le service qui ouvre ce PTY comme s'il s'agissait du RS485.
set -eu

PTY="${SERIAL_PORT:-/tmp/rs485}"

# Retenter la connexion tant que le simulateur n'écoute pas encore.
socat -d pty,raw,echo=0,link="$PTY" tcp:modbus-sim:9000,retry=30,interval=1 &

i=0
while [ ! -e "$PTY" ]; do
  i=$((i + 1))
  [ "$i" -gt 100 ] && echo "PTY $PTY jamais apparu" >&2 && exit 1
  sleep 0.1
done

exec python -m bridge
