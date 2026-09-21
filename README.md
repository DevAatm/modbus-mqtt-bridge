# Passerelle Modbus ↔ MQTT

Service Python conteneurisé, **unique maître** du lien vers l'équipement —
RS485 (Modbus RTU) ou réseau local (Modbus TCP, natif ou via passerelle).
Interroge l'équipement, publie les valeurs décodées en MQTT, et exécute les
écritures reçues en MQTT en les sérialisant avec les cycles de lecture.

L'équipement est entièrement décrit par un **profil YAML** — rien de
spécifique à un modèle donné ne vit dans le code. Voir `CLAUDE.md` pour les
invariants et contraintes de conception.

## Structure

```
src/bridge/
  domain/     types métier purs (values, profile, decoding, commands, result)
  ports/      interfaces Protocol (transport, publisher, clock)
  adapters/   pymodbus (série RTU / TCP), MQTT (aiomqtt), YAML
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
python -m bridge   # nécessite PROFILE, MQTT_HOST, et le lien (ci-dessous)
```

Ou via Docker : voir `docker-compose.yml`.

### Lien vers l'équipement

Le profil décrit l'équipement, l'environnement décrit le déploiement : le même
profil sert les deux liens.

| Variable | Défaut | Rôle |
|---|---|---|
| `MODBUS_TRANSPORT` | `serial` | `serial` ou `tcp` |
| `SERIAL_PORT` | — | requis si `serial` ; toujours par `/dev/serial/by-id/...` |
| `MODBUS_HOST` | — | requis si `tcp` |
| `MODBUS_PORT` | `502` | port TCP |
| `MODBUS_FRAMING` | `socket` | `socket` = équipement TCP natif, `rtu` = passerelle transparente TCP↔RS485 |
| `MODBUS_TIMEOUT` | `3` | secondes |

`MODBUS_FRAMING` dépend du matériel, pas du protocole applicatif : une
passerelle Waveshare/Elfin/USR encapsule des trames **RTU** dans du TCP. Un
mauvais choix ne donne pas de valeurs fausses, il donne des timeouts.

Sur un lien TCP, la reconnexion est automatique (backoff borné) et
l'`availability` MQTT bascule à `offline` après plusieurs lectures
consécutives en échec — HA cesse alors d'afficher les valeurs figées comme si
elles étaient fraîches. Aucune écriture n'est rejouée après une coupure.

⚠️ Modbus TCP n'a aucune authentification. En série, l'accès physique faisait
barrière ; sur le réseau, il faut un VLAN/segment isolé. `READ_ONLY=true`
(défaut) reste le garde-fou applicatif.

## Essais locaux (Mosquitto + Home Assistant + simulateur)

`docker-compose.dev.yml` monte une stack d'essai. **Ne pas utiliser en prod.**

```bash
# Boucle complète simulée : sim Modbus -> bridge -> MQTT -> HA
docker compose -f docker-compose.dev.yml --profile sim up --build

# Même boucle sur un lien Modbus TCP (ni socat ni PTY, image de production)
docker compose -f docker-compose.dev.yml --profile sim-tcp up --build
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
- **adapters** — pymodbus série RTU et TCP (`socket`/`rtu`, reconnexion
  automatique, exceptions → `Result`), MQTT
  aiomqtt (LWT `offline`, publication SI retain, souscription commandes,
  découverte Home Assistant générée du profil), chargeur/validateur YAML
  (échoue fort), horloge système.
- **app** — dispatcher (file sérialisée sous verrou, `preserve`, écriture
  bloc 0x10 mono- et multi-champ), poller (échéancier par bloc, santé fichier,
  bascule d'availability sur perte du lien),
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
