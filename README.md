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
  adapters/   pymodbus (série RTU / TCP), MQTT (aiomqtt), YAML (profil, bus)
  app/        poller, dispatcher, wiring (composition root), config
  __main__.py, healthcheck.py
profiles/     profils d'équipement
examples/     fichiers de bus d'exemple
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
python -m bridge   # nécessite PROFILE (ou BUS), MQTT_HOST, et le lien (ci-dessous)
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
| `MODBUS_PORT` | `502`, ou `8899` en `solarman_v5` | port TCP |
| `MODBUS_FRAMING` | `socket` | `socket`, `rtu` ou `solarman_v5` (voir ci-dessous) |
| `MODBUS_LOGGER_SERIAL` | — | requis par `solarman_v5` : n° de série du logger (décimal ou `0x...`) |
| `MODBUS_TIMEOUT` | `3` | secondes |

`MODBUS_FRAMING` dépend du matériel, pas du protocole applicatif :

- `socket` — équipement Modbus TCP natif (en-tête MBAP), port 502 ;
- `rtu` — passerelle transparente TCP↔RS485 (Waveshare, Elfin, USR...) qui
  encapsule des trames **RTU** dans du TCP ;
- `solarman_v5` — logger Sofar/Solarman **LSW-3 / LSE-3**, port 8899. Ce n'est
  pas du Modbus TCP : le logger attend un datagramme propriétaire préfixé de
  son propre numéro de série, dans lequel voyage la trame RTU. D'où
  `MODBUS_LOGGER_SERIAL`, à lire sur l'étiquette du logger ou dans son
  interface web.

Un mauvais choix ne donne pas de valeurs fausses, il donne des timeouts.

Le protocole Solarman V5 n'est pas documenté par le constructeur : il est
décrit par rétro-ingénierie. Confronter les premières valeurs publiées à
l'affichage de l'onduleur avant de les considérer comme justes.

Sur un lien TCP, la reconnexion est automatique (backoff borné) et
l'`availability` MQTT bascule à `offline` après plusieurs lectures
consécutives en échec — HA cesse alors d'afficher les valeurs figées comme si
elles étaient fraîches. Aucune écriture n'est rejouée après une coupure.

⚠️ Modbus TCP n'a aucune authentification. En série, l'accès physique faisait
barrière ; sur le réseau, il faut un VLAN/segment isolé. `READ_ONLY=true`
(défaut) reste le garde-fou applicatif.

## Profils d'équipement

Un profil décrit l'équipement : blocs de lecture groupée, points décodés, et
whitelist d'écriture. Il est validé au démarrage ; toute incohérence lève
`ProfileError` et le service ne démarre pas. Exemples : `profiles/sofar-esi-5k-s1.yaml`
(onduleur, holding, entiers mis à l'échelle), `profiles/eastron-sdm120.yaml`
(compteur, registres d'entrée, flottants).

### Bloc

| Champ      | Requis | Description |
|------------|--------|-------------|
| `name`     | oui    | Nom du bloc (logs). |
| `function` | non    | Table Modbus lue : `holding` (fonction 0x03, **défaut**) ou `input` (fonction 0x04). Toute autre valeur est rejetée. |
| `address`  | oui    | Premier registre (`0x0604` ou décimal). |
| `count`    | oui    | Nombre de registres lus en une requête. |
| `interval` | oui    | Période de lecture (`500ms`, `5s`, `1m`, `1h`). |
| `points`   | oui    | Liste non vide de points. |

Un profil sans `function` lit en holding, comme avant l'introduction du champ.
Les registres d'entrée sont en lecture seule côté Modbus : `function` ne
concerne que les blocs de lecture, les écritures visent toujours les holding.

```yaml
blocks:
  - name: instant
    function: input      # compteurs Eastron SDM : mesures en 0x04
    address: 0x0000
    count: 32
    interval: 5s
    points:
      - { key: voltage, offset: 0, type: f32, unit: V }
```

### Point

| Champ        | Requis | Description |
|--------------|--------|-------------|
| `key`        | oui    | Identifiant, unique dans le profil ; donne le topic `state/<key>`. |
| `offset`     | oui    | Position dans le bloc ; `offset` + largeur du type doit tenir dans `count`. |
| `type`       | oui    | Voir ci-dessous. |
| `scale`      | non    | Multiplicateur appliqué à la valeur brute (défaut `1`). |
| `unit`       | non    | Unité publiée. |
| `word_order` | non    | Types 32 bits : `big` (mot de poids fort en premier, **défaut**) ou `little`. |

| Type  | Registres | Contenu |
|-------|-----------|---------|
| `u16` | 1 | Entier non signé 16 bits. |
| `s16` | 1 | Entier signé 16 bits (complément à deux). |
| `u32` | 2 | Entier non signé 32 bits. |
| `s32` | 2 | Entier signé 32 bits. |
| `f32` | 2 | Flottant IEEE 754 simple précision. |

`f32` :

- big-endian par défaut (`word_order: big`) ; `little` pour les équipements
  qui inversent les deux mots ;
- pas d'échelle par défaut, mais `scale` reste appliqué s'il est présent
  (ex. `scale: 0.001` pour publier en kWh un compteur exprimé en Wh) ;
- une valeur **NaN ou infinie** n'est pas publiée : elle produit un
  avertissement dans les logs, la valeur retenue précédente reste en place ;
- **lecture seule** : `f32` est refusé dans `writes:` (single comme
  `layout`), la surface d'écriture n'est pas élargie par ce type.

## Plusieurs équipements sur un même bus

Un bus RS485 n'admet qu'**un seul maître**. Deux compteurs en guirlande sur
le même adaptateur USB-RS485 ne se servent donc pas avec deux conteneurs
(deux maîtres = trames qui se chevauchent, réponses lues par le mauvais
process), mais avec **un seul bridge** et un fichier de bus, pointé par `BUS` :

```yaml
# bus.yaml — cf. examples/bus-sdm120.yaml
devices:
  - { name: sdm120-pac,    profile: profiles/eastron-sdm120.yaml, slave_id: 1 }
  - { name: sdm120-ballon, profile: profiles/eastron-sdm120.yaml, slave_id: 2 }
```

- Le **profil** reste le modèle d'équipement, inchangé : le même
  `eastron-sdm120.yaml` sert aux deux compteurs.
- `name` et `slave_id` du fichier de bus **remplacent** ceux du profil.
  `name` donne les topics (`modbus/sdm120-pac/state/power`...) et le client
  MQTT (`modbus-sdm120-pac`).
- Chemins de profil absolus, ou relatifs au fichier de bus.
- `BUS` et `PROFILE` sont exclusifs. Sans `BUS`, `PROFILE` fonctionne comme
  avant (un équipement, nom et adresse du profil).

Le fichier est validé au démarrage ; le service refuse de démarrer
(`BusError`) si la liste est vide, si un `slave_id` est en double ou hors
1..247, si un `name` est en double ou inutilisable dans un topic, si un champ
est inconnu, si un profil référencé est invalide, ou si les profils n'ont pas
les mêmes réglages `serial` (ils partagent la ligne).

```yaml
services:
  modbus-bridge:
    build: .
    devices:
      - "/dev/serial/by-id/usb-FTDI_...-if00-port0:/dev/rs485"
    volumes:
      - ./profiles:/etc/bridge/profiles:ro
      - ./bus.yaml:/etc/bridge/bus.yaml:ro    # profils référencés en profiles/...
    environment:
      SERIAL_PORT: /dev/rs485
      BUS: /etc/bridge/bus.yaml               # à la place de PROFILE
      MQTT_HOST: mosquitto
      READ_ONLY: "true"
    mem_limit: 128m
    cpus: 0.25
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
    healthcheck:
      test: ["CMD", "python", "-m", "bridge.healthcheck"]
      interval: 60s
    restart: unless-stopped
```

Fonctionnement :

- **Une connexion, un verrou.** Le port (ou la socket) est ouvert une fois ;
  chaque équipement a sa file d'écritures et ses acquittements, mais tous
  partagent le même verrou : une seule trame à la fois sur la ligne.
- **Une availability par équipement.** Chaque équipement a son propre client
  MQTT, donc son propre LWT : un compteur débranché passe `offline` dans
  Home Assistant, l'autre reste `online`.
- **Fichier de santé partagé.** Il est rafraîchi dès qu'**un** équipement est
  lu avec succès. La perte d'un seul compteur est signalée par son
  availability, pas par un redémarrage du conteneur (qui couperait aussi les
  autres). Le healthcheck ne tombe que si plus aucun équipement ne répond.
- **Un équipement muet ralentit les autres.** Chaque lecture sans réponse
  occupe la ligne pendant le timeout et ses tentatives (plusieurs secondes) :
  les lectures des autres équipements sont retardées d'autant, pas perdues.

**En TCP**, le mode bus vaut derrière une passerelle transparente TCP↔RS485
(`MODBUS_TRANSPORT=tcp`, `MODBUS_FRAMING=rtu`) : plusieurs esclaves sur le
RS485 de la passerelle, une seule socket. Il est moins pertinent en `socket`
(un équipement Modbus TCP natif a en général sa propre adresse IP, donc son
propre lien) et en `solarman_v5` (un logger ne dessert que son onduleur).

## Essais locaux (Mosquitto + Home Assistant + simulateur)

`docker-compose.dev.yml` monte une stack d'essai. **Ne pas utiliser en prod.**

```bash
# Boucle complète simulée : sim Modbus -> bridge -> MQTT -> HA
docker compose -f docker-compose.dev.yml --profile sim up --build

# Même boucle sur un lien Modbus TCP (ni socat ni PTY, image de production)
docker compose -f docker-compose.dev.yml --profile sim-tcp up --build

# Même boucle derrière un logger Solarman simulé (enveloppe V5)
docker compose -f docker-compose.dev.yml --profile sim-solarman up --build

# Deux esclaves derrière une passerelle simulée, un seul bridge (BUS)
docker compose -f docker-compose.dev.yml --profile sim-bus up --build
```

Le simulateur sert plusieurs adresses avec `--slaves 1,2`
(`python -m tools.modbus_sim --slaves 1,2 tcp 5020 --framing rtu`) ; chaque
esclave a sa propre mémoire.

Le logger simulé ne valide que l'intégration : son enveloppe est écrite
d'après la même lecture du protocole que le framer du bridge. Seul un vrai
LSW-3/LSE-3 confirmera le format sur le fil.

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

- **domain** — value objects, décodage (`u16/s16/u32/s32/f32`, word order),
  encodage (inverse), validation des écritures (whitelist/allowed/clamp).
- **adapters** — pymodbus série RTU et TCP (`socket`/`rtu`/`solarman_v5`,
  reconnexion automatique, exceptions → `Result`), MQTT
  aiomqtt (LWT `offline`, publication SI retain, souscription commandes,
  découverte Home Assistant générée du profil), chargeur/validateur YAML
  (échoue fort), horloge système.
- **app** — dispatcher (file sérialisée sous verrou, `preserve`, écriture
  bloc 0x10 mono- et multi-champ), poller (échéancier par bloc, santé fichier,
  bascule d'availability sur perte du lien),
  wiring (`TaskGroup` poll + commandes, arrêt propre publiant `offline`),
  plusieurs équipements sur un même bus (`BUS`, verrou partagé, un client
  MQTT par équipement).

Écritures groupées : la fonction **0x10** est utilisée pour tout bloc, avec
read-modify-write (`preserve`). Une commande peut piloter **un seul** champ
(charge utile scalaire) ou **plusieurs offsets** d'un même bloc en une seule
trame 0x10 (charge utile JSON `{"active": -2000, "reactive": 0}`), chaque champ
identifié par sa `key` dans `layout`, validé (min/max) et clampé indépendamment.

Qualité : `ruff` clean, `mypy --strict` clean, `pytest` 154 tests verts (aucun
matériel/broker requis).


