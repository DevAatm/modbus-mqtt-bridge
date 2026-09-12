# Passerelle Modbus RS485 ↔ MQTT

Service Python conteneurisé, **unique maître** d'un bus RS485. Interroge un
équipement Modbus RTU, publie les valeurs décodées en MQTT, et exécute les
écritures reçues en MQTT en les sérialisant avec les cycles de lecture.

L'équipement est entièrement décrit par un **profil YAML** — rien de
spécifique à un modèle donné ne vit dans le code. Voir `CLAUDE.md` pour les
invariants et contraintes de conception.

## Structure

```
src/bridge/
  domain/     types métier purs (values, profile, decoding, commands, result)
  ports/      interfaces Protocol (transport, publisher, clock)
  adapters/   pymodbus, MQTT (aiomqtt), YAML
  app/        poller, dispatcher, wiring (composition root), config
  __main__.py, healthcheck.py
profiles/     profils d'équipement
tests/        décodage, validation des commandes, transport falsifié
```

## Développement

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ruff check . && ruff format --check .
mypy
pytest
```

## Exécution

```bash
python -m bridge   # nécessite SERIAL_PORT, MQTT_HOST, PROFILE
```

Ou via Docker : voir `docker-compose.yml`.

## Essais locaux (Mosquitto + Home Assistant + simulateur)

`docker-compose.dev.yml` monte une stack d'essai. **Ne pas utiliser en prod.**

```bash
# Boucle complète simulée : sim Modbus -> bridge -> MQTT -> HA
docker compose -f docker-compose.dev.yml --profile sim up --build
```

- Mosquitto : `localhost:1883` (anonyme), Home Assistant : http://localhost:8123
- Le profil `sim` démarre un **simulateur Modbus RTU** (`tools/modbus_sim.py`,
  bloc `battery` + zone de consignes) relié au bridge par un port série
  virtuel (socat over TCP) — aucun matériel requis.
- socat n'est présent que dans l'étage `dev` du Dockerfile ; l'image runtime
  reste minimale.
- Tester une écriture : mettre `READ_ONLY: "false"` dans le service
  `modbus-bridge` du compose de dev, puis publier sur
  `modbus/sofar-esi-5k-s1/command/power_setpoint` (voir résultat sur
  `.../command/power_setpoint/result`).

Sur **Docker Desktop / Windows**, seul le profil `sim` est exploitable :
passer un vrai port COM/USB dans un conteneur Linux n'est pas fiable. Pour un
essai avec le vrai onduleur, utiliser `docker-compose.yml` sur la cible Linux.

## État

Chaîne fonctionnelle de bout en bout :

- **domain** — value objects, décodage (`u16/s16/u32/s32`, word order),
  encodage (inverse), validation des écritures (whitelist/allowed/clamp).
- **adapters** — pymodbus RTU (`device_id`, exceptions → `Result`), MQTT
  aiomqtt (LWT `offline`, publication SI retain, souscription commandes,
  découverte Home Assistant générée du profil), chargeur/validateur YAML
  (échoue fort), horloge système.
- **app** — dispatcher (file sérialisée sous verrou, `preserve`, écriture
  bloc 0x10 mono- et multi-champ), poller (échéancier par bloc, santé fichier),
  wiring (`TaskGroup` poll + commandes, arrêt propre publiant `offline`).

Écritures groupées : la fonction **0x10** est utilisée pour tout bloc, avec
read-modify-write (`preserve`). Une commande peut piloter **un seul** champ
(charge utile scalaire) ou **plusieurs offsets** d'un même bloc en une seule
trame 0x10 (charge utile JSON `{"active": -2000, "reactive": 0}`), chaque champ
identifié par sa `key` dans `layout`, validé (min/max) et clampé indépendamment.

Qualité : `ruff` clean, `mypy --strict` clean, `pytest` 51 tests verts (aucun
matériel/broker requis).

### Reste à décider (non implémenté)

Le **watchdog de pilotage** (CLAUDE.md §Sécurité 4 : réémission périodique
d'une consigne + retour à un état sûr si le pilote se tait) n'est pas
implémenté : il exige d'étendre le schéma de profil (quelle clé, période,
valeur de repli). À spécifier avant codage plutôt qu'à deviner.
