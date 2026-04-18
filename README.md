# privacyIDEA FreeRADIUS Python Plugin (rlm_python3)

A Python-based FreeRADIUS plugin for two-factor authentication against
[privacyIDEA](https://www.privacyidea.org). Drop-in replacement for the
original Perl plugin (`privacyidea_radius.pm`) — uses the same INI config
format, the same environment variables, and produces identical RADIUS behavior.

## How it works

### Architecture overview

```
 RADIUS Client            FreeRADIUS                privacyIDEA
 (VPN, WiFi,        rlm_python3 module             API Server
  Switch...)        ┌──────────────────┐
                    │                  │
 Access-Request ──> │  authorize()     │
                    │    sets Auth-Type│
                    │                  │
                    │  authenticate()  │  POST /validate/check
                    │    reads INI     │ ─────────────────────>
                    │    builds params │                       
                    │    calls PI API  │ <─────────────────────
                    │    parses JSON   │  { result, detail }
                    │    maps attrs    │
                    │                  │
 Access-Accept  <── │  return (code,   │
 Access-Reject      │   reply_pairs,   │
 Access-Challenge   │   config_pairs)  │
                    └──────────────────┘
```

FreeRADIUS receives a RADIUS Access-Request from a network device (VPN
concentrator, WiFi controller, switch, etc.). The `rlm_python3` module loads
`privacyidea_radius.py` and calls its `authenticate()` function, passing all
RADIUS request attributes as a tuple of `(key, value)` pairs.

The plugin then:

1. **Reads configuration** from `rlm_python.ini` (loaded once at startup by
   `instantiate()`).
2. **Extracts credentials** from the RADIUS request (`User-Name`,
   `User-Password`, `State`, etc.).
3. **POSTs to privacyIDEA** `/validate/check` with `user`, `pass`, `realm`,
   `client`, and optionally `state`.
4. **Parses the JSON response** and decides the RADIUS outcome:
   - `result.value == true` -> **Access-Accept** (`RLM_MODULE_OK`)
   - `result.status == true` + `transaction_id` -> **Access-Challenge**
     (`RLM_MODULE_HANDLED`) for challenge-response flows (SMS, push, etc.)
   - `result.status == true`, no transaction -> **Access-Reject**
     (`RLM_MODULE_REJECT`)
   - `result.status == false` -> **server error** (`RLM_MODULE_FAIL`; error
     code 904 returns `RLM_MODULE_NOTFOUND`)
5. **Maps response attributes** from the privacyIDEA JSON into RADIUS reply
   attributes according to `[Mapping]` and `[Attribute]` sections in the INI.

### Authentication flow in detail

```
                  ┌───────────────────┐
                  │ RADIUS Client     │
                  │ sends user + OTP  │
                  └────────┬──────────┘
                           │ Access-Request
                           v
              ┌────────────────────────────┐
              │ FreeRADIUS                 │
              │  authorize section:        │
              │   -> python-privacyidea    │
              │   -> Auth-Type := python-  │
              │      privacyidea           │
              │                            │
              │  authenticate section:     │
              │   -> python-privacyidea    │
              │      calls authenticate(p) │
              └────────────┬───────────────┘
                           │
                           v
              ┌────────────────────────────┐
              │ privacyidea_radius.py      │
              │  1. Build params from      │
              │     RADIUS attributes      │
              │  2. POST to PI /validate/  │
              │     check                  │
              │  3. Parse JSON response    │
              └────────────┬───────────────┘
                           │
              ┌────────────┴───────────────┐
              │                            │
              v                            v
     result.value=true          result.status=true
     ┌──────────────┐          + transaction_id
     │ Access-Accept │          ┌──────────────────┐
     │ RLM_MODULE_OK │          │ Access-Challenge  │
     └──────────────┘          │ RLM_MODULE_HANDLED│
                               │ State=txn_id      │
                               └────────┬──────────┘
                                        │
                                        v
                               Client sends OTP
                               with State ──────> authenticate() again
                                                  (with state= param)
```

### Challenge-response (push, SMS, email)

When privacyIDEA returns `transaction_id` in its response, the plugin enters
challenge-response mode:

1. First request: user sends username + PIN (or empty password if
   `ADD_EMPTY_PASS=true`).
2. Plugin receives `transaction_id` from privacyIDEA, returns
   `Access-Challenge` with `State` = transaction_id and `Reply-Message` =
   challenge message.
3. Second request: client sends the OTP code with the `State` attribute.
4. Plugin decodes `State` back to the transaction_id and sends it as `state`
   parameter to privacyIDEA.
5. privacyIDEA validates and returns `result.value=true` -> `Access-Accept`.

## Project structure

```
rlm_pi/
├── privacyidea_radius.py              # The plugin (loaded by rlm_python3)
├── dictionary.netknights              # RADIUS dictionary (vendor NetKnights)
├── requirements.txt                   # Python deps: requests, chardet
├── Dockerfile                         # Alpine + FreeRADIUS + freeradius-python
├── docker-compose.yaml                # Single-service compose for freeradius
├── .env                               # Environment variables for compose
├── entrypoint.sh                      # Env vars -> INI config at startup
├── Makefile                           # build / up / down / logs targets
├── raddb/
│   ├── rlm_python.ini                 # Plugin configuration (INI)
│   ├── clients.conf                   # Default RADIUS clients (built into image)
│   ├── mods-available/
│   │   └── python-privacyidea        # FreeRADIUS module definition
│   ├── mods-enabled/
│   │   └── python-privacyidea -> ..  # Symlink (module enabled)
│   ├── sites-available/
│   │   └── privacyidea               # Virtual server definition
│   └── sites-enabled/
│       └── privacyidea -> ..         # Symlink (site enabled)
├── templates/
│   └── clients.conf                   # RADIUS clients (bind-mounted at runtime)
└── README.md
```

## Quick start

### 1. Configure

Edit `.env` — set your privacyIDEA server address:

```bash
RADIUS_PI_HOST=https://your-privacyidea-server
RADIUS_PI_SSLCHECK=false
RADIUS_DEBUG=false
```

Edit `templates/clients.conf` — add your NAS devices:

```
client my-vpn {
    ipaddr = 10.0.0.1/32
    secret = my-strong-secret
}
```

### 2. Build and start

```bash
make up
```

This runs `docker compose up -d --build` — builds the image from `Dockerfile`
and starts the container.

### 3. Verify

```bash
# Check container is running
make ps

# Follow logs
make logs

# Test authentication
radtest username 123456 127.0.0.1 0 testing123
```

### 4. Stop

```bash
make down
```

## Docker

### docker-compose.yaml

The compose file defines a single `freeradius` service:

```yaml
services:
  freeradius:
    build:
      context: .
      dockerfile: Dockerfile
    environment:
      RADIUS_PI_HOST: ${RADIUS_PI_HOST:-https://localhost}
      RADIUS_PI_SSLCHECK: ${RADIUS_PI_SSLCHECK:-false}
      RADIUS_PI_REALM: ${RADIUS_PI_REALM:-}
      RADIUS_PI_RESCONF: ${RADIUS_PI_RESCONF:-}
      RADIUS_PI_TIMEOUT: ${RADIUS_PI_TIMEOUT:-10}
      RADIUS_DEBUG: ${RADIUS_DEBUG:-false}
    ports:
      - "${RADIUS_PORT:-1812}:1812/tcp"
      - "${RADIUS_PORT:-1812}:1812/udp"
      - "${RADIUS_PORT_INC:-1813}:1813/udp"
    volumes:
      - ./templates/clients.conf:/etc/raddb/clients.conf:ro
    restart: unless-stopped
```

All settings come from `.env` and can be overridden per-environment.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `RADIUS_PI_HOST` | `https://localhost` | privacyIDEA server base URL (without `/validate/check`) |
| `RADIUS_PI_REALM` | (empty) | Default authentication realm |
| `RADIUS_PI_RESCONF` | (empty) | Resolver configuration |
| `RADIUS_PI_SSLCHECK` | `false` | Verify SSL certificate (`true`/`false`) |
| `RADIUS_PI_TIMEOUT` | `10` | HTTP request timeout in seconds |
| `RADIUS_DEBUG` | `false` | Enable DEBUG-level packet dumps (`true`/`false`). See [Logging and syslog](#logging-and-syslog). |
| `RADIUS_PORT` | `1812` | Host port for RADIUS auth (TCP + UDP) |
| `RADIUS_PORT_INC` | `1813` | Host port for RADIUS accounting (UDP) |
| `RADIUS_SYSLOG` | `true` | Enable syslog output from the Python plugin (in addition to `radiusd.radlog`) |
| `RADIUS_SYSLOG_HOST` | (empty) | Remote rsyslog host. Empty uses local `busybox syslogd` inside the container. |
| `RADIUS_SYSLOG_PORT` | `514` | Remote rsyslog port |
| `RADIUS_SYSLOG_PROTO` | `udp` | Transport: `udp` or `tcp` |
| `RADIUS_SYSLOG_FACILITY` | `auth` | Syslog facility: `auth`, `authpriv`, `daemon`, `local0`..`local7` |
| `RADIUS_SYSLOG_TAG` | `privacyidea-radius` | Syslog program name / ident |
| `RADIUS_SYSLOG_LEVEL` | `INFO` | Minimum level forwarded: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Must be `DEBUG` to see full-packet dumps from `RADIUS_DEBUG=true`. |

These are injected into `rlm_python.ini` by `entrypoint.sh` at container
startup.

### RADIUS clients (shared secrets)

The file `templates/clients.conf` is bind-mounted into the container at
`/etc/raddb/clients.conf`. Edit it to define which NAS devices can
authenticate:

```
client my-vpn-concentrator {
    ipaddr = 10.0.0.0/24
    secret = strong-random-secret-here
}

client my-wifi-controller {
    ipaddr = 192.168.1.10/32
    secret = another-secret
}
```

Changes to this file take effect after restarting the container
(`make down && make up`).

### Makefile targets

| Target | Command | Description |
|---|---|---|
| `make build` | `docker build` | Build image only (no compose) |
| `make up` | `docker compose up -d --build` | Build + start container |
| `make down` | `docker compose down` | Stop and remove container |
| `make logs` | `docker compose logs -f freeradius` | Follow container logs |
| `make ps` | `docker compose ps` | Show container status |
| `make push` | `docker tag` + `docker push` | Push image to registry |
| `make clean` | `down` + `rmi` | Stop container and remove image |

### What the Dockerfile does

1. Starts from `freeradius/freeradius-server:3.2.3-alpine`
2. Copies `raddb/` configuration files into `/etc/raddb/`
3. Copies `privacyidea_radius.py` into
   `/usr/share/privacyidea/freeradius/`
4. Copies `dictionary.netknights` into `/etc/raddb/dictionary`
5. Installs `python3`, `py3-requests`, `py3-chardet`,
   `freeradius-python`, `freeradius-utils` via apk
6. Removes default FreeRADIUS sites (`inner-tunnel`, `default`) and
   `eap` module
7. Sets `DEFAULT Auth-Type := python-privacyidea` in `/etc/raddb/users`

### What entrypoint.sh does

At container startup (before FreeRADIUS launches), `entrypoint.sh`:

1. Injects environment variables into `rlm_python.ini` via `sed`:

```
RADIUS_PI_HOST         -> URL = <value>/validate/check
RADIUS_PI_REALM        -> REALM = <value>
RADIUS_PI_RESCONF      -> RESCONF = <value>
RADIUS_PI_SSLCHECK     -> SSL_CHECK = <value>
RADIUS_PI_TIMEOUT      -> TIMEOUT = <value>
RADIUS_DEBUG           -> DEBUG = <value>
RADIUS_SYSLOG_HOST     -> SYSLOG_HOST = <value>
RADIUS_SYSLOG_PORT     -> SYSLOG_PORT = <value>
RADIUS_SYSLOG_PROTO    -> SYSLOG_PROTO = <value>
RADIUS_SYSLOG_FACILITY -> SYSLOG_FACILITY = <value>
RADIUS_SYSLOG_TAG      -> SYSLOG_TAG = <value>
RADIUS_SYSLOG_LEVEL    -> SYSLOG_LEVEL = <value>
```

2. Starts `busybox syslogd` in background (output to stdout for
   `docker logs`)
3. Launches FreeRADIUS in foreground mode (`radiusd -f`)

### Standalone docker run (without compose)

```bash
make build

docker run -d --rm --name privacyidea-radius \
    -p 1812:1812/udp \
    -p 1812:1812/tcp \
    -p 1813:1813/udp \
    -e RADIUS_PI_HOST=https://your-privacyidea-server \
    -e RADIUS_PI_SSLCHECK=false \
    -e RADIUS_DEBUG=true \
    privacyidea-freeradius-python:0.1.0
```

### Debug mode

To run FreeRADIUS with full debug output (`-X` flag), uncomment the
`command` lines in `docker-compose.yaml`:

```yaml
    command:
      - freeradius
      - -X
```

Or run directly:

```bash
docker compose run --rm freeradius freeradius -X
```

### Custom SSL CA certificates

To verify the privacyIDEA server's SSL certificate against a private CA,
mount the CA bundle and set `SSL_CA_PATH` in `rlm_python.ini`:

```yaml
    volumes:
      - ./templates/clients.conf:/etc/raddb/clients.conf:ro
      - ./certs/ca-bundle.crt:/etc/ssl/custom/ca-bundle.crt:ro
```

Then in `rlm_python.ini`:

```ini
SSL_CHECK = true
SSL_CA_PATH = /etc/ssl/custom
```

### Integrating with a full privacyIDEA stack

When running alongside privacyIDEA, MariaDB, and nginx in a separate
compose project, point `RADIUS_PI_HOST` to the reverse proxy and connect
both to the same Docker network:

```yaml
# in your privacyIDEA stack compose:
networks:
  privacyidea:
    name: privacyidea

# in this project's .env:
RADIUS_PI_HOST=https://reverse_proxy
```

Then add the external network to `docker-compose.yaml`:

```yaml
services:
  freeradius:
    # ... existing config ...
    networks:
      - privacyidea

networks:
  privacyidea:
    external: true
```

## Configuration

### INI file: `rlm_python.ini`

The INI file uses the same format as the original `rlm_perl.ini`. It is read
once at FreeRADIUS startup by `instantiate()`.

```ini
[Default]
URL = https://privacyidea.example.com/validate/check
REALM = myRealm
RESCONF =
SSL_CHECK = true
SSL_CA_PATH = /etc/ssl/certs
DEBUG = false
TIMEOUT = 10
CLIENTATTRIBUTE = Calling-Station-Id
SPLIT_NULL_BYTE = false
ADD_EMPTY_PASS = false
```

#### Configuration keys

| Key | Default | Description |
|---|---|---|
| `URL` | `https://127.0.0.1/validate/check` | Full URL of the privacyIDEA validate/check endpoint |
| `REALM` | (empty) | Default realm sent to privacyIDEA. If empty, the `Realm` from the RADIUS request is used |
| `RESCONF` | (empty) | Resolver configuration name |
| `DEBUG` | `FALSE` | Enable verbose debug logging |
| `SSL_CHECK` | `FALSE` | Verify the privacyIDEA server's SSL certificate |
| `SSL_CA_PATH` | (empty) | Path to CA certificates directory. If empty and `SSL_CHECK=true`, system CAs are used |
| `TIMEOUT` | `10` | HTTP request timeout in seconds |
| `CLIENTATTRIBUTE` | (empty) | RADIUS attribute to use as client IP (e.g. `Calling-Station-Id`). Overrides `NAS-IP-Address` |
| `SPLIT_NULL_BYTE` | `FALSE` | Split password on null byte and use only the first part (for certain PAP/EAP-GTC clients) |
| `ADD_EMPTY_PASS` | `FALSE` | Send an empty password if none is provided (for trigger-challenge flows) |
| `SERVICE_TYPE_MODE` | `permissive` | Service-Type handling mode (`strict` or `permissive`). See [Service-Type handling](#service-type-handling-rfc-2865) |
| `SYSLOG` | `TRUE` | Enable Python syslog logger (in addition to `radiusd.radlog`) |
| `SYSLOG_TAG` | `privacyidea-radius` | Syslog program name / ident |
| `SYSLOG_FACILITY` | `auth` | Syslog facility (`auth`, `authpriv`, `daemon`, `local0`..`local7`) |
| `SYSLOG_SOCKET` | (empty) | Override local syslog socket (else auto-detect `/dev/log` / `/var/run/syslog`) |
| `SYSLOG_HOST` | (empty) | Remote rsyslog host. Empty = local socket. |
| `SYSLOG_PORT` | `514` | Remote rsyslog port |
| `SYSLOG_PROTO` | `udp` | Remote transport: `udp` or `tcp` |
| `SYSLOG_LEVEL` | `INFO` | Minimum level forwarded: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

#### Auth-type overrides

Any `[Default]` key can be overridden per Auth-Type. Create an INI section
named after the Auth-Type value:

```ini
[Default]
URL = https://pi-primary.example.com/validate/check

[scope1]
URL = https://pi-secondary.example.com/validate/check
REALM = specialRealm
```

When `Auth-Type = scope1`, the URL and REALM from the `[scope1]` section are
used instead of `[Default]`.

### Attribute mapping

The INI file supports two mapping mechanisms to add privacyIDEA response
fields to the RADIUS reply.

#### Simple mapping: `[Mapping]` and `[Mapping <subobject>]`

Maps fields from the privacyIDEA JSON response `detail` object directly to
RADIUS attributes.

```ini
# detail.serial -> privacyIDEA-Serial
[Mapping]
serial = privacyIDEA-Serial

# detail.user.group -> Class
[Mapping user]
group = Class
```

How it works internally:
- `[Mapping]` reads `decoded["detail"][key]` and sets it as the RADIUS
  attribute named in the value.
- `[Mapping user]` reads `decoded["detail"]["user"][key]`.

#### Regex-based mapping: `[Attribute <name>]`

For advanced scenarios where you need to extract substrings from multi-value
attributes using regular expressions.

```ini
[Attribute Filter-Id]
dir = user
userAttribute = acl
regex = CN=(\w*)-users,OU=sales,DC=example,DC=com
prefix =
suffix =
```

This reads `detail.user.acl`, iterates over all values (if it is an array),
applies the regex, and if group `$1` matches, adds
`prefix + $1 + suffix` as the RADIUS attribute `Filter-Id`.

| Key | Description |
|---|---|
| `dir` | Sub-object in `detail` to look in (e.g. `user`). Empty = top-level `detail` |
| `userAttribute` | Key name in the privacyIDEA response |
| `regex` | Regular expression with capture group `(...)` |
| `prefix` | String prepended to the match result |
| `suffix` | String appended to the match result |
| `radiusAttribute` | (optional) Override the RADIUS attribute name instead of using the section name |

Example with override:

```ini
[Attribute myCustomRule]
radiusAttribute = Filter-Id
userAttribute = user-resolver
regex = resolver1
prefix = FIXEDValue
suffix =
```

This sets `Filter-Id = FIXEDValue` if the user's resolver matches `resolver1`.

### FreeRADIUS module definition

The file `raddb/mods-available/python-privacyidea` tells FreeRADIUS how to
load the Python plugin:

```
python python-privacyidea {
    python_path = /usr/share/privacyidea/freeradius
    module = privacyidea_radius

    mod_instantiate = ${.module}
    func_instantiate = instantiate

    mod_authenticate = ${.module}
    func_authenticate = authenticate

    mod_authorize = ${.module}
    func_authorize = authorize

    ...

    config {
        configfile = /etc/raddb/rlm_python.ini
    }
}
```

- `python_path` — directory containing `privacyidea_radius.py`
- `module` — Python module name (filename without `.py`)
- `config { configfile = ... }` — passed to `instantiate()` so the plugin
  knows which INI file to load

### Virtual server definition

`raddb/sites-available/privacyidea` defines the RADIUS virtual server:

```
server {
    authorize {
        update request {
            Packet-Src-IP-Address = "%{Packet-Src-IP-Address}"
        }
        python-privacyidea
        if (ok || updated) {
            update control {
                Auth-Type := python-privacyidea
            }
        }
    }
    listen {
        type = auth
        ipaddr = *
        port = 0
    }
    authenticate {
        Auth-Type python-privacyidea {
            python-privacyidea
        }
    }
}
```

The `authorize` section runs the plugin first (returns OK), then sets
`Auth-Type` so that the `authenticate` section calls the same plugin for the
actual authentication.

### RADIUS clients

`raddb/clients.conf` defines which network devices are allowed to send RADIUS
requests:

```
client dockernet {
    ipaddr = 127.0.0.1/32
    secret = testing123
}
```

In production, add your NAS devices here with proper shared secrets.

## Service-Type handling (RFC 2865)

The RADIUS `Service-Type` attribute (Type 6) tells the server what kind of
service the NAS is requesting. The plugin supports two modes controlled by
`SERVICE_TYPE_MODE` in the INI config.

### Supported Service-Type values

| Value | Name | RFC | Description |
|---|---|---|---|
| 1 | Login | 2865 | Full login session |
| 2 | Framed | 2865 | PPP/SLIP/tunneled session (VPN) |
| 8 | Authenticate-Only | 2865 | Verify credentials only, NAS handles authorization locally |
| 17 | Authorize-Only | 5765 | User already authenticated, return authorization attributes only |

### permissive mode (default)

```ini
SERVICE_TYPE_MODE = permissive
```

Ignores `Service-Type` entirely. Every request goes through full
authentication via privacyIDEA **and** returns mapped attributes in the reply.
This is the safest default — it works with all NAS devices regardless of how
they set `Service-Type`.

```
Any Service-Type ──> authenticate via PI ──> return auth result + mapped attrs
```

### strict mode

```ini
SERVICE_TYPE_MODE = strict
```

Follows RFC 2865 semantics. Behavior depends on `Service-Type`:

```
Service-Type=8 (Authenticate Only)
  ──> authenticate via privacyIDEA
  ──> return ONLY auth result (Accept/Reject)
  ──> NO mapped attributes (no Framed-IP-Address, Filter-Id, Class, etc.)

Service-Type=17 (Authorize Only)
  ──> skip privacyIDEA /validate/check entirely (no password needed)
  ──> return Access-Accept immediately
  ──> NAS or other FreeRADIUS modules provide authorization attributes

Service-Type=1,2,... (all others)
  ──> full authentication + attribute mapping (same as permissive)
```

### Choosing the right mode

| Scenario | Recommended mode | Why |
|---|---|---|
| Single RADIUS request per connection (most VPNs, WiFi) | `permissive` | NAS expects auth + attrs in one response |
| NAS sends separate auth and authz requests | `strict` | Follow RFC, return attrs only when asked |
| Cisco ISE / ACS with service-type awareness | `strict` | ISE uses Service-Type correctly |
| Legacy NAS, unknown behavior | `permissive` | Safe fallback |

### Service-Type=8 + Framed-IP-Address

A common question: can you get `Framed-IP-Address` from privacyIDEA with
`Service-Type=8`?

| Mode | Behavior |
|---|---|
| `permissive` | **Yes** — attributes are always mapped, including `Framed-IP-Address` |
| `strict` | **No** — RFC 2865 says Authenticate-Only should not return authorization attributes. Use `Service-Type=2` (Framed) or a separate Authorize-Only request |

If your NAS sends `Service-Type=8` but you need authorization attributes in
the same response, use `permissive` mode.

## Plugin internals

### Module lifecycle

| FreeRADIUS event | Python function | What it does |
|---|---|---|
| Module load | `instantiate(p)` | Reads `configfile` from FreeRADIUS config, loads INI, populates global `CONFIG` |
| Authorize phase | `authorize(p)` | Returns `RLM_MODULE_OK` (pass-through) |
| Authenticate phase | `authenticate(p)` | Full authentication logic (see below) |
| Module unload | `detach()` | Cleanup logging |

### authenticate() step by step

1. **Convert request** — FreeRADIUS passes `p` as a tuple of
   `(key, value)` pairs. Converted to a dict for easy access.

2. **Load auth-type config** — If the RADIUS request has a specific
   `Auth-Type`, load overrides from the matching INI section.

3. **Check Service-Type** — Read `Service-Type` from the request.
   - If `Service-Type=17` (Authorize Only) and `strict` mode: skip
     authentication, return `RLM_MODULE_OK` immediately (handled by
     `_handle_authorize_only()`).
   - Otherwise: continue to step 4.

4. **Build params** (`_build_params()`):
   - Extract user (`Stripped-User-Name` > `User-Name`)
   - Extract password (with null-byte split, encoding detection)
   - Extract State (hex-decode for challenge-response)
   - Determine client IP (`CLIENTATTRIBUTE` > `NAS-IP-Address` >
     `Packet-Src-IP-Address`)
   - Determine realm (`REALM` from INI > `Realm` from request)

5. **Echo Message-Authenticator** — If present in the request, copy it to the
   reply for security.

6. **POST to privacyIDEA** — `requests.Session.post()` with
   `User-Agent: FreeRADIUS`, SSL verification settings, and timeout from
   config.

7. **Parse response** (`_handle_pi_response()`) — Evaluate `result.value`,
   `result.status`, `detail.transaction_id`, and `result.error.code`.

8. **Map attributes** (conditional) — Run `[Mapping]` and `[Attribute]` rules
   from the INI. In `strict` mode with `Service-Type=8`, this step is
   **skipped** (authentication only, no authorization attributes).

9. **Return** — `(return_code, reply_pairs, config_pairs)` to FreeRADIUS.

### Return codes

| Code | Constant | Meaning |
|---|---|---|
| 0 | `RLM_MODULE_REJECT` | Authentication denied |
| 1 | `RLM_MODULE_FAIL` | Server error or HTTP failure |
| 2 | `RLM_MODULE_OK` | Authentication successful |
| 3 | `RLM_MODULE_HANDLED` | Challenge-response issued |
| 6 | `RLM_MODULE_NOTFOUND` | User not found (PI error 904) |

## Logging and syslog

The plugin emits every log line through `_log(level, msg)` which writes to
**both**:

1. **FreeRADIUS** via `radiusd.radlog(level, msg)` — visible in `docker logs`
   and the FreeRADIUS log file.
2. **Python syslog** via `logging.handlers.SysLogHandler` — forwarded to a
   local syslogd or a remote rsyslog server, depending on `SYSLOG_HOST`.

### Two log tiers

| Level | Contents |
|-------|----------|
| `INFO` (default) | One line per operational event: auth request summary (user/realm/client), challenge issued with transaction id, challenge response received, access granted with token serial, access denied, PI internal error, accounting Start/Stop/Interim. Safe for production. |
| `DEBUG` | Everything at INFO, plus full-packet dumps (see below). Verbose. |

Set `SYSLOG_LEVEL=DEBUG` (or `RADIUS_SYSLOG_LEVEL=DEBUG` via env) to forward
debug traffic to your rsyslog server. `RADIUS_DEBUG=true` / INI `DEBUG=TRUE`
must also be set to emit the DEBUG lines at all — the level filter comes after
the emit decision.

### Full-packet DEBUG dumps

With `DEBUG=TRUE` in the INI (or `RADIUS_DEBUG=true` in the container env),
the plugin logs:

| Log prefix | Source | When |
|------------|--------|------|
| `RAD_REQUEST: <attr> = <value>` | Incoming Access-Request | `authenticate()` start |
| `urlparam <key> = <value>` | privacyIDEA POST body fields | Before HTTP call |
| `PI HTTP >>> POST <url> headers=… body=…` | Outbound HTTP request | Before `session.post()` |
| `PI HTTP <<< <status> <reason> headers=… body=…` | Inbound HTTP response | After `session.post()` |
| `Content <json>` | Full PI JSON response body | In `_handle_pi_response()` |
| `RADIUS reply <<< code=… reply=… config=…` | Outbound RADIUS response | End of `authenticate()` / `_handle_authorize_only()` |
| `ACCT_REQUEST: <attr> = <value>` | Incoming Accounting-Request | `accounting()` start |

### Secret redaction (always on)

Packet dumps are **redacted by default** — there is no opt-in toggle. Before
any field is emitted, its key name is lowercased and compared against a list
of known-secret substrings. Matching values are replaced with `***`:

```
password, pass,
chap-challenge, chap-response, chap-password,
mschap, ms-chap,
authorization, cookie, token, secret
```

What this covers:

- RADIUS `User-Password`, `CHAP-Password`, `CHAP-Challenge`, `MS-CHAP-*`
  attributes in `RAD_REQUEST` / `ACCT_REQUEST` dumps
- The `pass` field in the privacyIDEA POST body (`urlparam`, `PI HTTP >>>`)
- `Authorization` / `Set-Cookie` response headers
- `token` / `password` / `secret` fields anywhere in the JSON response body
  (walked recursively; arrays and nested objects handled)
- Values in the outgoing RADIUS reply whose attribute names match the list

JSON bodies that fail to parse are logged verbatim (not JSON, nothing to
redact). The redaction list is deliberately conservative — review the actual
DEBUG output in a test environment before forwarding to a central aggregator
in production.

### Quick test with a local UDP listener

```bash
# on the host
nc -u -l 1514
```

```ini
# in rlm_python.ini (or via env vars)
DEBUG = TRUE
SYSLOG_HOST = host.docker.internal
SYSLOG_PORT = 1514
SYSLOG_LEVEL = DEBUG
```

Fire a test auth (`radtest user pass 127.0.0.1 0 testing123`) — the listener
prints every attribute with secrets as `***`.

## Differences from the Perl plugin

| Aspect | Perl (`privacyidea_radius.pm`) | Python (`privacyidea_radius.py`) |
|---|---|---|
| Runtime | `rlm_perl` | `rlm_python3` |
| HTTP client | `LWP::UserAgent` | `requests` (auto URL-encoding via `data=`) |
| Encoding detection | `Encode::Guess` | `chardet` |
| URI encoding | `URI::Encode` (manual) | Not needed (`requests` handles `data=`) |
| Config parser | `Config::IniFiles` | `configparser` (stdlib) |
| Build dependency | `perl -MCPAN -e 'install URI::Encode'` | None (all via `apk`) |
| INI format | Same | Same (backward compatible) |
| Env variables | Same | Same |
| Service-Type | Not supported | `strict` / `permissive` mode for RFC 2865 Service-Type handling |
| FreeRADIUS interface | `%RAD_REQUEST`, `%RAD_REPLY` globals | `(code, reply_tuple, config_tuple)` return |

## Production notes

- **SSL**: Set `RADIUS_PI_SSLCHECK=true` in production. Mount your CA
  certificates and set `SSL_CA_PATH` in the INI if using private CAs.
- **Shared secret**: Change `testing123` in `clients.conf` to a strong random
  secret. Add entries for each NAS device.
- **Debug logging**: Keep `RADIUS_DEBUG=false` in production. Debug mode logs
  passwords and full response bodies.
- **Timeout**: Default is 10 seconds. Adjust based on your privacyIDEA
  server's response time. Users will wait this long before seeing a RADIUS
  timeout.
- **Dictionary**: `dictionary.netknights` defines the `privacyIDEA-Serial`
  vendor-specific attribute (vendor ID 44929). This is included in the image
  at `/etc/raddb/dictionary`.

## License

GPLv2
