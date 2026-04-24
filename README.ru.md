🌐 [English](README.md) | **Русский**

# Плагин privacyIDEA FreeRADIUS на Python (rlm_python3)

Python-плагин FreeRADIUS для двухфакторной аутентификации через
[privacyIDEA](https://www.privacyidea.org). Прямая замена оригинального
Perl-плагина (`privacyidea_radius.pm`) — использует тот же формат INI-конфигурации,
те же переменные окружения и обеспечивает идентичное поведение RADIUS.

## Как это работает

### Обзор архитектуры

```
 RADIUS-клиент           FreeRADIUS                privacyIDEA
 (VPN, WiFi,        модуль rlm_python3             API-сервер
  коммутатор...)    ┌──────────────────┐
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

FreeRADIUS получает RADIUS Access-Request от сетевого устройства (VPN-концентратор,
WiFi-контроллер, коммутатор и т.д.). Модуль `rlm_python3` загружает
`privacyidea_radius.py` и вызывает его функцию `authenticate()`, передавая все
атрибуты RADIUS-запроса в виде кортежа пар `(key, value)`.

Затем плагин:

1. **Читает конфигурацию** из `rlm_python.ini` (загружается один раз при запуске
   функцией `instantiate()`).
2. **Извлекает учётные данные** из RADIUS-запроса (`User-Name`,
   `User-Password`, `State` и др.).
3. **Отправляет POST в privacyIDEA** `/validate/check` с параметрами `user`, `pass`, `realm`,
   `client` и опционально `state`.
4. **Разбирает JSON-ответ** и определяет результат RADIUS:
   - `result.value == true` -> **Access-Accept** (`RLM_MODULE_OK`)
   - `result.status == true` + `transaction_id` -> **Access-Challenge**
     (`RLM_MODULE_HANDLED`) для потоков challenge-response (SMS, push и др.)
   - `result.status == true`, нет transaction -> **Access-Reject**
     (`RLM_MODULE_REJECT`)
   - `result.status == false` -> **ошибка сервера** (`RLM_MODULE_FAIL`; код ошибки
     904 возвращает `RLM_MODULE_NOTFOUND`)
5. **Маппит атрибуты ответа** из JSON privacyIDEA в атрибуты RADIUS-ответа
   согласно секциям `[Mapping]` и `[Attribute]` в INI.

### Детальный поток аутентификации

```
                  ┌───────────────────┐
                  │ RADIUS-клиент     │
                  │ отправляет        │
                  │ user + OTP        │
                  └────────┬──────────┘
                           │ Access-Request
                           v
              ┌────────────────────────────┐
              │ FreeRADIUS                 │
              │  секция authorize:        │
              │   -> python-privacyidea    │
              │   -> Auth-Type := python-  │
              │      privacyidea           │
              │                            │
              │  секция authenticate:     │
              │   -> python-privacyidea    │
              │      вызывает              │
              │      authenticate(p)       │
              └────────────┬───────────────┘
                           │
                           v
              ┌────────────────────────────┐
              │ privacyidea_radius.py      │
              │  1. Формирует параметры    │
              │     из атрибутов RADIUS    │
              │  2. POST в PI /validate/   │
              │     check                  │
              │  3. Разбирает JSON-ответ   │
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
                               Клиент отправляет OTP
                               с State ──────> authenticate() снова
                                                  (с параметром state=)
```

### Challenge-response (push, SMS, email)

Когда privacyIDEA возвращает `transaction_id` в ответе, плагин переходит
в режим challenge-response:

1. Первый запрос: пользователь отправляет username + PIN (или пустой пароль, если
   `ADD_EMPTY_PASS=true`).
2. Плагин получает `transaction_id` от privacyIDEA, возвращает
   `Access-Challenge` с `State` = transaction_id и `Reply-Message` =
   текст challenge.
3. Второй запрос: клиент отправляет OTP-код с атрибутом `State`.
4. Плагин декодирует `State` обратно в transaction_id и отправляет его как параметр `state`
   в privacyIDEA.
5. privacyIDEA валидирует и возвращает `result.value=true` -> `Access-Accept`.

## Структура проекта

```
rlm_pi/
├── privacyidea_radius.py              # Плагин (загружается rlm_python3)
├── dictionary.netknights              # RADIUS-словарь (вендор NetKnights)
├── requirements.txt                   # Python-зависимости: requests, chardet
├── Dockerfile                         # Alpine + FreeRADIUS + freeradius-python
├── docker-compose.yaml                # Compose с одним сервисом freeradius
├── .env                               # Переменные окружения для compose
├── entrypoint.sh                      # Переменные окружения -> INI-конфиг при запуске
├── Makefile                           # Цели build / up / down / logs
├── raddb/
│   ├── rlm_python.ini                 # Конфигурация плагина (INI)
│   ├── clients.conf                   # RADIUS-клиенты по умолчанию (встроены в образ)
│   ├── mods-available/
│   │   └── python-privacyidea        # Определение модуля FreeRADIUS
│   ├── mods-enabled/
│   │   └── python-privacyidea -> ..  # Симлинк (модуль включён)
│   ├── sites-available/
│   │   └── privacyidea               # Определение виртуального сервера
│   └── sites-enabled/
│       └── privacyidea -> ..         # Симлинк (сайт включён)
├── templates/
│   └── clients.conf                   # RADIUS-клиенты (монтируется при запуске)
└── README.md
```

## Быстрый старт

### 1. Настройка

Отредактируйте `.env` — укажите адрес сервера privacyIDEA:

```bash
RADIUS_PI_HOST=https://your-privacyidea-server
RADIUS_PI_SSLCHECK=false
RADIUS_DEBUG=false
```

Отредактируйте `templates/clients.conf` — добавьте ваши NAS-устройства:

```
client my-vpn {
    ipaddr = 10.0.0.1/32
    secret = my-strong-secret
}
```

### 2. Сборка и запуск

```bash
make up
```

Выполняет `docker compose up -d --build` — собирает образ из `Dockerfile`
и запускает контейнер.

### 3. Проверка

```bash
# Проверка работы контейнера
make ps

# Просмотр логов
make logs

# Тест аутентификации
radtest username 123456 127.0.0.1 0 testing123
```

### 4. Остановка

```bash
make down
```

## Docker

### docker-compose.yaml

Compose-файл определяет один сервис `freeradius`:

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

Все настройки берутся из `.env` и могут быть переопределены для каждой среды.

### Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `RADIUS_PI_HOST` | `https://localhost` | Базовый URL сервера privacyIDEA (без `/validate/check`) |
| `RADIUS_PI_REALM` | (пусто) | Realm аутентификации по умолчанию |
| `RADIUS_PI_RESCONF` | (пусто) | Конфигурация резолвера |
| `RADIUS_PI_SSLCHECK` | `false` | Проверка SSL-сертификата (`true`/`false`) |
| `RADIUS_PI_TIMEOUT` | `10` | Таймаут HTTP-запроса в секундах |
| `RADIUS_DEBUG` | `false` | Включение DEBUG-дампов пакетов (`true`/`false`). См. [Логирование и syslog](#логирование-и-syslog). |
| `RADIUS_PORT` | `1812` | Порт хоста для RADIUS auth (TCP + UDP) |
| `RADIUS_PORT_INC` | `1813` | Порт хоста для RADIUS accounting (UDP) |
| `RADIUS_SYSLOG` | `true` | Включение syslog-вывода из Python-плагина (в дополнение к `radiusd.radlog`) |
| `RADIUS_SYSLOG_HOST` | (пусто) | Удалённый rsyslog-хост. Пусто = локальный `busybox syslogd` внутри контейнера. |
| `RADIUS_SYSLOG_PORT` | `514` | Порт удалённого rsyslog |
| `RADIUS_SYSLOG_PROTO` | `udp` | Транспорт: `udp` или `tcp` |
| `RADIUS_SYSLOG_FACILITY` | `auth` | Syslog facility: `auth`, `authpriv`, `daemon`, `local0`..`local7` |
| `RADIUS_SYSLOG_TAG` | `privacyidea-radius` | Имя программы / ident в syslog |
| `RADIUS_SYSLOG_LEVEL` | `INFO` | Минимальный уровень пересылки: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Должен быть `DEBUG` для полных дампов пакетов при `RADIUS_DEBUG=true`. |

Эти переменные подставляются в `rlm_python.ini` скриптом `entrypoint.sh` при
запуске контейнера.

### RADIUS-клиенты (общие секреты)

Файл `templates/clients.conf` монтируется в контейнер по пути
`/etc/raddb/clients.conf`. Отредактируйте его для определения устройств,
которым разрешена аутентификация:

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

Изменения в этом файле вступают в силу после перезапуска контейнера
(`make down && make up`).

### Цели Makefile

| Цель | Команда | Описание |
|---|---|---|
| `make build` | `docker build` | Только сборка образа (без compose) |
| `make up` | `docker compose up -d --build` | Сборка + запуск контейнера |
| `make down` | `docker compose down` | Остановка и удаление контейнера |
| `make logs` | `docker compose logs -f freeradius` | Просмотр логов контейнера |
| `make ps` | `docker compose ps` | Статус контейнера |
| `make push` | `docker tag` + `docker push` | Отправка образа в реестр |
| `make clean` | `down` + `rmi` | Остановка контейнера и удаление образа |

### Что делает Dockerfile

1. Базовый образ `freeradius/freeradius-server:3.2.3-alpine`
2. Копирует конфигурационные файлы `raddb/` в `/etc/raddb/`
3. Копирует `privacyidea_radius.py` в
   `/usr/share/privacyidea/freeradius/`
4. Копирует `dictionary.netknights` в `/etc/raddb/dictionary`
5. Устанавливает `python3`, `py3-requests`, `py3-chardet`,
   `freeradius-python`, `freeradius-utils` через apk
6. Удаляет стандартные сайты FreeRADIUS (`inner-tunnel`, `default`) и
   модуль `eap`
7. Устанавливает `DEFAULT Auth-Type := python-privacyidea` в `/etc/raddb/users`

### Что делает entrypoint.sh

При запуске контейнера (перед запуском FreeRADIUS) `entrypoint.sh`:

1. Подставляет переменные окружения в `rlm_python.ini` через `sed`:

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

2. Запускает `busybox syslogd` в фоне (вывод в stdout для
   `docker logs`)
3. Запускает FreeRADIUS в режиме переднего плана (`radiusd -f`)

### Автономный запуск docker run (без compose)

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

### Режим отладки

Для запуска FreeRADIUS с полным отладочным выводом (флаг `-X`), раскомментируйте
строки `command` в `docker-compose.yaml`:

```yaml
    command:
      - freeradius
      - -X
```

Или запустите напрямую:

```bash
docker compose run --rm freeradius freeradius -X
```

### Пользовательские SSL CA-сертификаты

Для верификации SSL-сертификата сервера privacyIDEA с использованием частного CA,
смонтируйте CA-бандл и задайте `SSL_CA_PATH` в `rlm_python.ini`:

```yaml
    volumes:
      - ./templates/clients.conf:/etc/raddb/clients.conf:ro
      - ./certs/ca-bundle.crt:/etc/ssl/custom/ca-bundle.crt:ro
```

Затем в `rlm_python.ini`:

```ini
SSL_CHECK = true
SSL_CA_PATH = /etc/ssl/custom
```

### Интеграция с полным стеком privacyIDEA

При совместной работе с privacyIDEA, MariaDB и nginx в отдельном
compose-проекте, направьте `RADIUS_PI_HOST` на реверс-прокси и подключите
оба к одной Docker-сети:

```yaml
# в compose вашего стека privacyIDEA:
networks:
  privacyidea:
    name: privacyidea

# в .env этого проекта:
RADIUS_PI_HOST=https://reverse_proxy
```

Затем добавьте внешнюю сеть в `docker-compose.yaml`:

```yaml
services:
  freeradius:
    # ... существующая конфигурация ...
    networks:
      - privacyidea

networks:
  privacyidea:
    external: true
```

## Конфигурация

### INI-файл: `rlm_python.ini`

INI-файл использует тот же формат, что и оригинальный `rlm_perl.ini`. Он читается
один раз при запуске FreeRADIUS функцией `instantiate()`.

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

#### Ключи конфигурации

| Ключ | По умолчанию | Описание |
|---|---|---|
| `URL` | `https://127.0.0.1/validate/check` | Полный URL endpoint-а privacyIDEA validate/check |
| `REALM` | (пусто) | Realm по умолчанию, отправляемый в privacyIDEA. Если пуст, используется `Realm` из RADIUS-запроса |
| `RESCONF` | (пусто) | Имя конфигурации резолвера |
| `DEBUG` | `FALSE` | Включение подробного отладочного логирования |
| `SSL_CHECK` | `FALSE` | Проверка SSL-сертификата сервера privacyIDEA |
| `SSL_CA_PATH` | (пусто) | Путь к каталогу CA-сертификатов. Если пуст и `SSL_CHECK=true`, используются системные CA |
| `TIMEOUT` | `10` | Таймаут HTTP-запроса в секундах |
| `CLIENTATTRIBUTE` | (пусто) | RADIUS-атрибут для IP клиента (например, `Calling-Station-Id`). Переопределяет `NAS-IP-Address` |
| `SPLIT_NULL_BYTE` | `FALSE` | Разделение пароля по нулевому байту с использованием только первой части (для некоторых PAP/EAP-GTC клиентов) |
| `ADD_EMPTY_PASS` | `FALSE` | Отправка пустого пароля при его отсутствии (для потоков trigger-challenge) |
| `SERVICE_TYPE_MODE` | `permissive` | Режим обработки Service-Type (`strict` или `permissive`). См. [Обработка Service-Type](#обработка-service-type-rfc-2865) |
| `SYSLOG` | `TRUE` | Включение Python syslog-логгера (в дополнение к `radiusd.radlog`) |
| `SYSLOG_TAG` | `privacyidea-radius` | Имя программы / ident в syslog |
| `SYSLOG_FACILITY` | `auth` | Syslog facility (`auth`, `authpriv`, `daemon`, `local0`..`local7`) |
| `SYSLOG_SOCKET` | (пусто) | Переопределение локального syslog-сокета (иначе автоопределение `/dev/log` / `/var/run/syslog`) |
| `SYSLOG_HOST` | (пусто) | Удалённый rsyslog-хост. Пусто = локальный сокет. |
| `SYSLOG_PORT` | `514` | Порт удалённого rsyslog |
| `SYSLOG_PROTO` | `udp` | Удалённый транспорт: `udp` или `tcp` |
| `SYSLOG_LEVEL` | `INFO` | Минимальный уровень пересылки: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

#### Переопределения по Auth-Type

Любой ключ `[Default]` может быть переопределён для конкретного Auth-Type. Создайте INI-секцию
с именем, соответствующим значению Auth-Type:

```ini
[Default]
URL = https://pi-primary.example.com/validate/check

[scope1]
URL = https://pi-secondary.example.com/validate/check
REALM = specialRealm
```

При `Auth-Type = scope1` URL и REALM из секции `[scope1]` используются
вместо `[Default]`.

### Маппинг атрибутов

INI-файл поддерживает два механизма маппинга для добавления полей ответа privacyIDEA
в RADIUS-ответ.

#### Простой маппинг: `[Mapping]` и `[Mapping <subobject>]`

Отображает поля из объекта `detail` JSON-ответа privacyIDEA непосредственно в
RADIUS-атрибуты.

```ini
# detail.serial -> privacyIDEA-Serial
[Mapping]
serial = privacyIDEA-Serial

# detail.user.group -> Class
[Mapping user]
group = Class
```

Как это работает внутри:
- `[Mapping]` читает `decoded["detail"][key]` и устанавливает его как RADIUS-атрибут
  с именем из значения.
- `[Mapping user]` читает `decoded["detail"]["user"][key]`.

#### Маппинг на основе регулярных выражений: `[Attribute <name>]`

Для продвинутых сценариев, когда нужно извлечь подстроки из многозначных
атрибутов с помощью регулярных выражений.

```ini
[Attribute Filter-Id]
dir = user
userAttribute = acl
regex = CN=(\w*)-users,OU=sales,DC=example,DC=com
prefix =
suffix =
```

Читает `detail.user.acl`, перебирает все значения (если это массив),
применяет regex, и если группа `$1` совпадает, добавляет
`prefix + $1 + suffix` как RADIUS-атрибут `Filter-Id`.

| Ключ | Описание |
|---|---|
| `dir` | Подобъект в `detail` для поиска (например, `user`). Пусто = верхний уровень `detail` |
| `userAttribute` | Имя ключа в ответе privacyIDEA |
| `regex` | Регулярное выражение с группой захвата `(...)` |
| `prefix` | Строка, добавляемая перед результатом |
| `suffix` | Строка, добавляемая после результата |
| `radiusAttribute` | (опционально) Переопределение имени RADIUS-атрибута вместо имени секции |

Пример с переопределением:

```ini
[Attribute myCustomRule]
radiusAttribute = Filter-Id
userAttribute = user-resolver
regex = resolver1
prefix = FIXEDValue
suffix =
```

Устанавливает `Filter-Id = FIXEDValue`, если резолвер пользователя совпадает с `resolver1`.

### Определение модуля FreeRADIUS

Файл `raddb/mods-available/python-privacyidea` указывает FreeRADIUS, как
загружать Python-плагин:

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

- `python_path` — каталог, содержащий `privacyidea_radius.py`
- `module` — имя Python-модуля (имя файла без `.py`)
- `config { configfile = ... }` — передаётся в `instantiate()`, чтобы плагин
  знал, какой INI-файл загружать

### Определение виртуального сервера

`raddb/sites-available/privacyidea` определяет виртуальный RADIUS-сервер:

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

Секция `authorize` сначала запускает плагин (возвращает OK), затем устанавливает
`Auth-Type`, чтобы секция `authenticate` вызвала тот же плагин для
фактической аутентификации.

### RADIUS-клиенты

`raddb/clients.conf` определяет, каким сетевым устройствам разрешено отправлять RADIUS-запросы:

```
client dockernet {
    ipaddr = 127.0.0.1/32
    secret = testing123
}
```

В production добавьте сюда ваши NAS-устройства с надёжными shared secret.

## Обработка Service-Type (RFC 2865)

RADIUS-атрибут `Service-Type` (Type 6) сообщает серверу, какой тип
сервиса запрашивает NAS. Плагин поддерживает два режима, управляемых
параметром `SERVICE_TYPE_MODE` в INI-конфигурации.

### Поддерживаемые значения Service-Type

| Значение | Имя | RFC | Описание |
|---|---|---|---|
| 1 | Login | 2865 | Полная сессия входа |
| 2 | Framed | 2865 | PPP/SLIP/туннелированная сессия (VPN) |
| 8 | Authenticate-Only | 2865 | Только проверка учётных данных, NAS обрабатывает авторизацию локально |
| 17 | Authorize-Only | 5765 | Пользователь уже аутентифицирован, вернуть только атрибуты авторизации |

### Режим permissive (по умолчанию)

```ini
SERVICE_TYPE_MODE = permissive
```

Полностью игнорирует `Service-Type`. Каждый запрос проходит полную
аутентификацию через privacyIDEA **и** возвращает маппированные атрибуты в ответе.
Это самый безопасный режим по умолчанию — работает со всеми NAS-устройствами независимо от того,
как они устанавливают `Service-Type`.

```
Любой Service-Type ──> аутентификация через PI ──> возврат результата + маппированные атрибуты
```

### Режим strict

```ini
SERVICE_TYPE_MODE = strict
```

Следует семантике RFC 2865. Поведение зависит от `Service-Type`:

```
Service-Type=8 (Authenticate Only)
  ──> аутентификация через privacyIDEA
  ──> возврат ТОЛЬКО результата аутентификации (Accept/Reject)
  ──> БЕЗ маппированных атрибутов (без Framed-IP-Address, Filter-Id, Class и т.д.)

Service-Type=17 (Authorize Only)
  ──> пропуск privacyIDEA /validate/check (пароль не нужен)
  ──> немедленный возврат Access-Accept
  ──> NAS или другие модули FreeRADIUS предоставляют атрибуты авторизации

Service-Type=1,2,... (все остальные)
  ──> полная аутентификация + маппинг атрибутов (как в permissive)
```

### Выбор правильного режима

| Сценарий | Рекомендуемый режим | Причина |
|---|---|---|
| Один RADIUS-запрос на соединение (большинство VPN, WiFi) | `permissive` | NAS ожидает auth + атрибуты в одном ответе |
| NAS отправляет отдельные запросы auth и authz | `strict` | Следование RFC, атрибуты только по запросу |
| Cisco ISE / ACS с поддержкой service-type | `strict` | ISE корректно использует Service-Type |
| Устаревший NAS, неизвестное поведение | `permissive` | Безопасный fallback |

### Service-Type=8 + Framed-IP-Address

Частый вопрос: можно ли получить `Framed-IP-Address` от privacyIDEA с
`Service-Type=8`?

| Режим | Поведение |
|---|---|
| `permissive` | **Да** — атрибуты всегда маппируются, включая `Framed-IP-Address` |
| `strict` | **Нет** — RFC 2865 указывает, что Authenticate-Only не должен возвращать атрибуты авторизации. Используйте `Service-Type=2` (Framed) или отдельный Authorize-Only запрос |

Если ваш NAS отправляет `Service-Type=8`, но вам нужны атрибуты авторизации в
том же ответе, используйте режим `permissive`.

## Внутренности плагина

### Жизненный цикл модуля

| Событие FreeRADIUS | Python-функция | Что делает |
|---|---|---|
| Загрузка модуля | `instantiate(p)` | Читает `configfile` из конфигурации FreeRADIUS, загружает INI, заполняет глобальный `CONFIG` |
| Фаза Authorize | `authorize(p)` | Возвращает `RLM_MODULE_OK` (pass-through) |
| Фаза Authenticate | `authenticate(p)` | Полная логика аутентификации (см. ниже) |
| Выгрузка модуля | `detach()` | Очистка логирования |

### authenticate() пошагово

1. **Конвертация запроса** — FreeRADIUS передаёт `p` как кортеж
   пар `(key, value)`. Конвертируется в dict для удобного доступа.

2. **Загрузка конфигурации auth-type** — Если RADIUS-запрос содержит конкретный
   `Auth-Type`, загружаются переопределения из соответствующей INI-секции.

3. **Проверка Service-Type** — Чтение `Service-Type` из запроса.
   - Если `Service-Type=17` (Authorize Only) и режим `strict`: пропуск
     аутентификации, немедленный возврат `RLM_MODULE_OK` (обрабатывается
     `_handle_authorize_only()`).
   - Иначе: переход к шагу 4.

4. **Формирование параметров** (`_build_params()`):
   - Извлечение пользователя (`Stripped-User-Name` > `User-Name`)
   - Извлечение пароля (с разделением по нулевому байту, определением кодировки)
   - Извлечение State (hex-декодирование для challenge-response)
   - Определение IP клиента (`CLIENTATTRIBUTE` > `NAS-IP-Address` >
     `Packet-Src-IP-Address`)
   - Определение realm (`REALM` из INI > `Realm` из запроса)

5. **Эхо Message-Authenticator** — Если присутствует в запросе, копируется в
   ответ для безопасности.

6. **POST в privacyIDEA** — `requests.Session.post()` с
   `User-Agent: FreeRADIUS`, настройками SSL-верификации и таймаутом из
   конфигурации.

7. **Разбор ответа** (`_handle_pi_response()`) — Оценка `result.value`,
   `result.status`, `detail.transaction_id` и `result.error.code`.

8. **Маппинг атрибутов** (условно) — Выполнение правил `[Mapping]` и `[Attribute]`
   из INI. В режиме `strict` с `Service-Type=8` этот шаг **пропускается**
   (только аутентификация, без атрибутов авторизации).

9. **Возврат** — `(return_code, reply_pairs, config_pairs)` в FreeRADIUS.

### Коды возврата

| Код | Константа | Значение |
|---|---|---|
| 0 | `RLM_MODULE_REJECT` | Аутентификация отклонена |
| 1 | `RLM_MODULE_FAIL` | Ошибка сервера или HTTP-сбой |
| 2 | `RLM_MODULE_OK` | Аутентификация успешна |
| 3 | `RLM_MODULE_HANDLED` | Выдан challenge-response |
| 6 | `RLM_MODULE_NOTFOUND` | Пользователь не найден (ошибка PI 904) |

## Логирование и syslog

Плагин отправляет каждую строку лога через `_log(level, msg)`, которая пишет в
**оба**:

1. **FreeRADIUS** через `radiusd.radlog(level, msg)` — видно в `docker logs`
   и лог-файле FreeRADIUS.
2. **Python syslog** через `logging.handlers.SysLogHandler` — пересылается в
   локальный syslogd или удалённый rsyslog-сервер в зависимости от `SYSLOG_HOST`.

### Два уровня логирования

| Уровень | Содержимое |
|---------|----------|
| `INFO` (по умолчанию) | Одна строка на операционное событие: сводка запроса аутентификации (user/realm/client), выданный challenge с transaction id, полученный ответ challenge, доступ предоставлен с серийным номером токена, доступ отклонён, внутренняя ошибка PI, accounting Start/Stop/Interim. Безопасно для production. |
| `DEBUG` | Всё из INFO, плюс полные дампы пакетов (см. ниже). Подробно. |

Установите `SYSLOG_LEVEL=DEBUG` (или `RADIUS_SYSLOG_LEVEL=DEBUG` через env) для пересылки
отладочного трафика на ваш rsyslog-сервер. `RADIUS_DEBUG=true` / INI `DEBUG=TRUE`
также должны быть установлены для генерации DEBUG-строк — фильтр уровня применяется после
решения о генерации.

### Полные DEBUG-дампы пакетов

При `DEBUG=TRUE` в INI (или `RADIUS_DEBUG=true` в env контейнера),
плагин логирует:

| Префикс лога | Источник | Когда |
|------------|--------|------|
| `RAD_REQUEST: <attr> = <value>` | Входящий Access-Request | Начало `authenticate()` |
| `urlparam <key> = <value>` | Поля POST-тела privacyIDEA | Перед HTTP-вызовом |
| `PI HTTP >>> POST <url> headers=… body=…` | Исходящий HTTP-запрос | Перед `session.post()` |
| `PI HTTP <<< <status> <reason> headers=… body=…` | Входящий HTTP-ответ | После `session.post()` |
| `Content <json>` | Полное тело JSON-ответа PI | В `_handle_pi_response()` |
| `RADIUS reply <<< code=… reply=… config=…` | Исходящий RADIUS-ответ | Конец `authenticate()` / `_handle_authorize_only()` |
| `ACCT_REQUEST: <attr> = <value>` | Входящий Accounting-Request | Начало `accounting()` |

### Редактирование секретов (всегда включено)

Дампы пакетов **редактируются по умолчанию** — нет отдельного переключателя. Перед
выводом любого поля его имя ключа приводится к нижнему регистру и сравнивается со списком
известных секретных подстрок. Совпадающие значения заменяются на `***`:

```
password, pass,
chap-challenge, chap-response, chap-password,
mschap, ms-chap,
authorization, cookie, token, secret
```

Что покрывается:

- RADIUS-атрибуты `User-Password`, `CHAP-Password`, `CHAP-Challenge`, `MS-CHAP-*`
  в дампах `RAD_REQUEST` / `ACCT_REQUEST`
- Поле `pass` в POST-теле privacyIDEA (`urlparam`, `PI HTTP >>>`)
- Заголовки ответа `Authorization` / `Set-Cookie`
- Поля `token` / `password` / `secret` в любом месте JSON-тела ответа
  (обход рекурсивный; массивы и вложенные объекты обрабатываются)
- Значения в исходящем RADIUS-ответе, чьи имена атрибутов совпадают со списком

JSON-тела, которые не удаётся распарсить, логируются как есть (не JSON, нечего
редактировать). Список редактирования намеренно консервативен — проверьте реальный
DEBUG-вывод в тестовой среде перед пересылкой в центральный агрегатор
в production.

### Быстрый тест с локальным UDP-слушателем

```bash
# на хосте
nc -u -l 1514
```

```ini
# в rlm_python.ini (или через env vars)
DEBUG = TRUE
SYSLOG_HOST = host.docker.internal
SYSLOG_PORT = 1514
SYSLOG_LEVEL = DEBUG
```

Запустите тестовую аутентификацию (`radtest user pass 127.0.0.1 0 testing123`) — слушатель
выведет каждый атрибут с секретами как `***`.

## Отличия от Perl-плагина

| Аспект | Perl (`privacyidea_radius.pm`) | Python (`privacyidea_radius.py`) |
|---|---|---|
| Среда выполнения | `rlm_perl` | `rlm_python3` |
| HTTP-клиент | `LWP::UserAgent` | `requests` (автоматическое URL-кодирование через `data=`) |
| Определение кодировки | `Encode::Guess` | `chardet` |
| URI-кодирование | `URI::Encode` (ручное) | Не требуется (`requests` обрабатывает `data=`) |
| Парсер конфигурации | `Config::IniFiles` | `configparser` (stdlib) |
| Зависимость сборки | `perl -MCPAN -e 'install URI::Encode'` | Нет (всё через `apk`) |
| Формат INI | Тот же | Тот же (обратная совместимость) |
| Переменные окружения | Те же | Те же |
| Service-Type | Не поддерживается | Режимы `strict` / `permissive` для обработки Service-Type по RFC 2865 |
| Интерфейс FreeRADIUS | Глобальные `%RAD_REQUEST`, `%RAD_REPLY` | Возврат `(code, reply_tuple, config_tuple)` |

## Заметки для production

- **SSL**: Установите `RADIUS_PI_SSLCHECK=true` в production. Смонтируйте ваши CA-сертификаты
  и задайте `SSL_CA_PATH` в INI при использовании частных CA.
- **Shared secret**: Замените `testing123` в `clients.conf` на надёжный случайный
  секрет. Добавьте записи для каждого NAS-устройства.
- **Отладочное логирование**: Держите `RADIUS_DEBUG=false` в production. Режим отладки логирует
  пароли и полные тела ответов.
- **Таймаут**: По умолчанию 10 секунд. Настройте в соответствии со временем ответа
  вашего сервера privacyIDEA. Пользователи будут ждать это время до
  таймаута RADIUS.
- **Словарь**: `dictionary.netknights` определяет vendor-specific атрибут `privacyIDEA-Serial`
  (vendor ID 44929). Он включён в образ по пути `/etc/raddb/dictionary`.

## Лицензия

GPLv2
