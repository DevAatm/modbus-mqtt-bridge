# CLAUDE.md — Passerelle Modbus RS485 ↔ MQTT

Service Python conteneurisé. Il est **l'unique maître** d'un bus RS485,
interroge périodiquement un équipement Modbus RTU, publie les valeurs
décodées en MQTT, et exécute les demandes d'écriture reçues en MQTT en
les sérialisant avec les cycles de lecture.

Cible de référence : onduleur hybride Sofar ESI-5K-S1 (protocole
Sofar Modbus-G3 V1.35). Mais **rien de spécifique à Sofar ne doit
exister dans le code** : l'équipement est entièrement décrit par un
profil YAML.

---

## Invariant central

> Un seul processus, un seul thread, une seule socket série parlent au bus.

Le RS485 est half-duplex sans arbitrage. Deux maîtres = trames
corrompues et timeouts aléatoires. Tout accès au port passe par une
unique tâche asyncio propriétaire du transport. Lectures et écritures
sont sérialisées dans la même file.

Corollaires :

- Aucun autre code n'ouvre le port série. Jamais.
- Les écritures s'intercalent **entre** deux cycles de poll, pas pendant.
- Si le déploiement expose aussi un dongle WiFi constructeur, c'est un
  second maître potentiel : le documenter, pas l'ignorer.

---

## Architecture — ports et adaptateurs

Découpage identique à ce que je pratique en .NET. Le domaine ne connaît
ni pymodbus, ni paho, ni asyncio-mqtt.

```
src/<pkg>/
  domain/          # types métier purs, zéro I/O, zéro dépendance externe
    values.py      #   Quantity, RegisterAddress, DeviceId… (value objects)
    profile.py     #   modèle du profil : PointSpec, BlockSpec, WriteSpec
    decoding.py    #   registres bruts -> grandeurs SI (fonctions pures)
    commands.py    #   WriteCommand, validation, clamp, whitelist
  ports/           # Protocol classes — les interfaces du domaine
    transport.py   #   ModbusTransport
    publisher.py   #   MessagePublisher
    clock.py       #   Clock
  adapters/
    modbus_rtu.py  #   pymodbus -> ModbusTransport
    mqtt.py        #   MQTT -> MessagePublisher + souscription commandes
    profile_yaml.py#   YAML -> domain.profile
  app/
    poller.py      #   boucle de poll, propriétaire du bus
    dispatcher.py  #   file de commandes, sérialisation lecture/écriture
    wiring.py      #   composition root, le seul endroit qui instancie
  __main__.py
```

Règles :

- `domain/` et `ports/` n'importent rien de `adapters/` ni `app/`.
- Un adaptateur implémente un `Protocol`, jamais l'inverse.
- Le décodage est une **fonction pure** : `(registres, PointSpec) -> valeur`.
  Testable sans matériel, et c'est là que va l'essentiel des tests.
- Pragmatisme mono-paquet : un seul package installable, pas de découpage
  en distributions séparées. Les frontières sont logiques, pas physiques.

Si un test a besoin d'un vrai port série ou d'un vrai broker, c'est que
la frontière est au mauvais endroit.

---

## Le profil YAML est le produit

C'est lui qui rend le service réutilisable sur un autre onduleur ou un
compteur. Il doit pouvoir tout décrire sans toucher au code.

```yaml
device:
  name: sofar-esi-5k-s1
  protocol: modbus-g3-v1.35
  slave_id: 1
  serial: { baudrate: 9600, bytesize: 8, parity: N, stopbits: 1 }

# Lectures groupées : un read par bloc, pas un par point.
blocks:
  - name: battery
    address: 0x0604
    count: 7
    interval: 5s
    points:
      - { key: battery_voltage, offset: 0, type: u16, scale: 0.1, unit: V }
      - { key: battery_current, offset: 1, type: s16, scale: 0.01, unit: A }
      - { key: battery_power,   offset: 2, type: s16, scale: 10,   unit: W }
      - { key: battery_temp,    offset: 3, type: s16, unit: "°C" }
      - { key: battery_soc,     offset: 4, type: u16, unit: "%" }
      - { key: battery_soh,     offset: 5, type: u16, unit: "%" }

# Écritures : whitelist explicite. Ce qui n'est pas listé est interdit.
writes:
  - key: storage_mode
    address: 0x1110
    type: u16
    mode: single
    allowed: [0, 1, 2, 3]

  - key: power_setpoint
    address: 0x1187
    count: 6            # écriture groupée obligatoire (fonction 0x10)
    mode: block
    layout:
      - { offset: 2, type: s16, scale: 10, min: -5000, max: 5000 }
    preserve: true      # les offsets non listés sont relus et réécrits tels quels
```

Points d'attention imposés par le matériel :

- **Signé vs non signé.** Une puissance batterie est négative en décharge.
  `s16` et `u16` sont deux types distincts, jamais un `int` nu.
- **Écritures groupées.** Certains blocs refusent l'écriture d'un seul
  registre (`IllegalDataAddressError 0x02`). Le profil déclare `mode: block`,
  l'adaptateur utilise la fonction 0x10 et réécrit le bloc complet.
- **`preserve`.** Pour un bloc partiellement piloté, relire puis réécrire
  en ne modifiant que les offsets ciblés.
- **Valeurs sur deux registres.** Prévoir `u32`/`s32` avec ordre de mots
  explicite (`word_order: big|little`).
- **Échelles.** Toujours dans le profil, jamais en dur. Le code manipule
  des grandeurs SI.

Valider le profil au démarrage contre un schéma, et **échouer fort** si
invalide. Un profil douteux ne doit pas produire un service qui écrit
n'importe où.

---

## MQTT

Topics en unités SI, un topic par point.

```
<prefix>/<device>/state/<key>          # valeurs (retain)
<prefix>/<device>/availability         # online/offline (LWT, retain)
<prefix>/<device>/command/<key>        # demandes d'écriture
<prefix>/<device>/command/<key>/result # acquittement
```

- **LWT obligatoire** : `offline` en testament, `online` à la connexion.
  Sans ça, HA affiche des valeurs figées comme si elles étaient fraîches.
- **Découverte Home Assistant** : publier les messages de discovery
  générés depuis le profil (`homeassistant/sensor/.../config`), afin
  qu'ajouter un point au YAML suffise à créer l'entité. C'est ce qui rend
  le déploiement multi-site reproductible.
- **Acquittement des commandes** : toute demande reçue produit un résultat
  publié (accepté / rejeté avec motif / échoué avec l'exception Modbus).
  Un silence n'est pas une réponse.
- Les consommateurs en aval (HA, routeur solaire, superviseur) ne parlent
  que MQTT. Le Modbus s'arrête à ce service.

---

## Sécurité des écritures

Non négociable — ce service pilote une installation électrique chez des
tiers.

1. **Whitelist.** Seules les clés déclarées dans `writes:` sont écrivables.
   Une commande sur une clé inconnue est rejetée, pas devinée.
2. **Clamp et validation.** `min`/`max`/`allowed` du profil appliqués avant
   tout envoi. Hors bornes = rejet explicite, pas de troncature silencieuse.
3. **Jamais de paramètres de couplage réseau.** Code pays, seuils de
   protection, courbes de découplage : hors de portée du service, même
   si les registres existent.
4. **Watchdog.** Une consigne de pilotage est réémise périodiquement et
   assortie d'un retour à un état sûr si le pilote se tait. Un service
   qui meurt ne doit pas laisser l'équipement sur sa dernière consigne
   indéfiniment.
5. **Le pilotage ajoute, il ne retire jamais.** Les sécurités existantes
   de l'équipement restent en place et prioritaires.
6. **Mode `read_only`** activable par configuration, et par défaut à `true`.

---

## Conventions Python

- Python 3.12+, `asyncio`, typage strict partout, `mypy --strict` propre.
- `CancellationToken` → passer et respecter l'annulation asyncio ;
  toute boucle doit s'arrêter proprement sur `CancelledError`.
- Erreurs typées plutôt qu'exceptions traversantes dans le domaine :
  `Result[T, E]` (une petite union suffit, pas de dépendance lourde).
  Les exceptions restent aux frontières d'I/O, converties en `Result`
  par l'adaptateur.
- Value objects immuables : `@dataclass(frozen=True, slots=True)`,
  construction par factory statique qui valide (`RegisterAddress.parse`).
  Pas de primitive obsession dans le domaine.
- Une classe par cas d'usage. Pas de classe fourre-tout `ModbusService`.
- `ruff format` + `ruff check` en hook PostToolUse.
- Logs structurés, niveau configurable, jamais de secret en clair.

---

## Tests

- **Décodage** : tables de cas `registres -> grandeur attendue`, dérivées
  de relevés réels. C'est le filet principal.
- **Transport falsifié** : un `FakeTransport` implémentant le `Protocol`,
  scriptable (réponses, exceptions Modbus, timeouts).
- **Sérialisation** : vérifier qu'aucune écriture ne part pendant un poll,
  et qu'une rafale de commandes ne saute pas son tour.
- **Profil** : profils invalides rejetés au chargement.
- Pas de test qui exige du matériel. Un banc réel reste utile, hors CI.

---

## Conteneur — contrainte d'empreinte

Cible de déploiement : mini PC Lenovo ThinkCentre M710q Tiny, i3-6100T,
8 Go de RAM, partagé avec Home Assistant, Mosquitto, et le reste de la
pile. **Ce service doit être le plus discret du lot.**

Budget à tenir : moins de 150 Mo d'image, moins de 50 Mo de RSS en
régime établi, CPU négligeable entre deux polls.

### Dépendances

Liste courte et fermée : `pymodbus`, `pyserial`, un client MQTT,
`PyYAML`. Rien d'autre sans justification écrite.

Interdits de fait : `numpy`, `pandas`, tout framework web, tout ORM,
toute bibliothèque de configuration « à tout faire ». Un décodage de
registre 16 bits se fait avec `struct` et des opérateurs de décalage.

Client MQTT : préférer `aiomqtt` (fin, asyncio natif) à `paho` piloté
depuis une boucle — moins de code de pontage, un thread en moins.

### Image

Build multi-étapes, venv construit dans l'étage builder puis copié.
L'image finale ne contient ni `pip`, ni compilateur, ni cache.

```dockerfile
FROM python:3.12-slim AS builder
RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
COPY --from=builder /venv /venv
COPY src/ /app/src/
WORKDIR /app
RUN useradd -r -u 1000 bridge && usermod -aG dialout bridge
USER bridge
ENV PATH=/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "bridge"]
```

Alpine donnerait une image plus petite encore, et toutes les
dépendances ci-dessus sont compatibles — mais musl force la compilation
de ce qui n'a pas de wheel, ce qui allonge le build et complique le
diagnostic. `slim` est le bon compromis ici ; ne basculer que si
l'image dépasse réellement le budget.

### Comportement à l'exécution

- Pas de serveur HTTP pour le healthcheck : écrire un horodatage dans
  un fichier à chaque lecture réussie, et le tester en `healthcheck`.
  Un framework web pour exposer `/health`, c'est plusieurs dizaines de
  mégaoctets pour une ligne d'information.
- Pas de polling actif inutile : `asyncio.sleep` entre les cycles, jamais
  de boucle serrée. Entre deux polls le processus doit être réellement au
  repos.
- Pas d'historique en mémoire. Ce service publie, il ne stocke pas.
  La rétention est le travail du recorder de HA ou d'une base dédiée.
- Logs plafonnés au niveau du démon Docker, sinon ils rempliront le SSD
  de 128 Go en quelques mois.

```yaml
services:
  modbus-bridge:
    build: .
    devices:
      - "/dev/serial/by-id/usb-FTDI_...-if00-port0:/dev/rs485"
    volumes:
      - ./profiles/sofar-esi-5k-s1.yaml:/etc/bridge/profile.yaml:ro
    environment:
      SERIAL_PORT: /dev/rs485
      MQTT_HOST: mosquitto
      PROFILE: /etc/bridge/profile.yaml
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

Le `mem_limit` n'est pas décoratif : il transforme une fuite mémoire en
redémarrage du conteneur plutôt qu'en machine qui s'écroule et emporte
Home Assistant avec elle.

Le port série est exposé par `devices:`, **pas** par `volumes:`, et
référencé par `/dev/serial/by-id/...` — jamais `/dev/ttyUSB0`, dont
l'ordre d'énumération change dès qu'un second adaptateur est branché.
L'utilisateur du conteneur doit appartenir au groupe propriétaire du
périphérique (`dialout` sur Debian) : sinon, permission refusée malgré
un mapping correct.

---

## Ce qu'il ne faut pas faire

- Mettre une adresse de registre ou une échelle en dur dans le code.
- Ouvrir le port série ailleurs que dans le propriétaire du bus.
- Écrire un registre non déclaré dans le profil, même « pour tester ».
- Publier une valeur non décodée, ou dans une unité non SI.
- Supposer qu'une réponse Modbus valide signifie une valeur correcte :
  confronter aux relevés de l'équipement avant de figer un point.
- Traiter une absence de réponse comme une valeur nulle.