#
#    privacyIDEA FreeRADIUS Python plugin (rlm_python3)
#
#    Drop-in replacement for privacyidea_radius.pm
#    Uses the same INI config format (rlm_perl.ini / rlm_python.ini)
#
#    Copyright (C) 2025
#    License: GPLv2
#

from __future__ import annotations

import configparser
import binascii
import logging
import logging.handlers
import os
import re
import time
from typing import Any

import requests

# radiusd module is only available inside FreeRADIUS runtime.
# When running standalone (tests, debugging), we use a stub.
try:
    import radiusd
    _HAS_RADIUSD = True
except ImportError:
    _HAS_RADIUSD = False

try:
    import chardet
except ImportError:
    chardet = None

# ---------------------------------------------------------------------------
# FreeRADIUS return codes (provided by radiusd module at runtime)
# ---------------------------------------------------------------------------
RLM_MODULE_REJECT = 0
RLM_MODULE_FAIL = 1
RLM_MODULE_OK = 2
RLM_MODULE_HANDLED = 3
RLM_MODULE_INVALID = 4
RLM_MODULE_USERLOCK = 5
RLM_MODULE_NOTFOUND = 6
RLM_MODULE_NOOP = 7
RLM_MODULE_UPDATED = 8
RLM_MODULE_NUMCODES = 9

RET_NAMES = {
    0: "RLM_MODULE_REJECT",
    1: "RLM_MODULE_FAIL",
    2: "RLM_MODULE_OK",
    3: "RLM_MODULE_HANDLED",
    4: "RLM_MODULE_INVALID",
    5: "RLM_MODULE_USERLOCK",
    6: "RLM_MODULE_NOTFOUND",
    7: "RLM_MODULE_NOOP",
    8: "RLM_MODULE_UPDATED",
    9: "RLM_MODULE_NUMCODES",
}

# ---------------------------------------------------------------------------
# Log level constants — match FreeRADIUS radiusd levels
# ---------------------------------------------------------------------------
L_DBG = 1
L_AUTH = 2
L_INFO = 3
L_ERR = 4
L_PROXY = 5
L_ACCT = 6

# Map our log levels to Python logging levels
_LEVEL_TO_PYTHON = {
    L_DBG:   logging.DEBUG,
    L_AUTH:  logging.INFO,
    L_INFO:  logging.INFO,
    L_ERR:   logging.ERROR,
    L_PROXY: logging.INFO,
    L_ACCT:  logging.INFO,
}

# Map our log levels to human-readable names (for syslog messages)
_LEVEL_NAMES = {
    L_DBG:   "DEBUG",
    L_AUTH:  "AUTH",
    L_INFO:  "INFO",
    L_ERR:   "ERROR",
    L_PROXY: "PROXY",
    L_ACCT:  "ACCT",
}

# ---------------------------------------------------------------------------
# Syslog setup
# ---------------------------------------------------------------------------
_SYSLOG_FACILITY_MAP = {
    "auth":     logging.handlers.SysLogHandler.LOG_AUTH,
    "authpriv": logging.handlers.SysLogHandler.LOG_AUTHPRIV,
    "daemon":   logging.handlers.SysLogHandler.LOG_DAEMON,
    "local0":   logging.handlers.SysLogHandler.LOG_LOCAL0,
    "local1":   logging.handlers.SysLogHandler.LOG_LOCAL1,
    "local2":   logging.handlers.SysLogHandler.LOG_LOCAL2,
    "local3":   logging.handlers.SysLogHandler.LOG_LOCAL3,
    "local4":   logging.handlers.SysLogHandler.LOG_LOCAL4,
    "local5":   logging.handlers.SysLogHandler.LOG_LOCAL5,
    "local6":   logging.handlers.SysLogHandler.LOG_LOCAL6,
    "local7":   logging.handlers.SysLogHandler.LOG_LOCAL7,
}

_syslog: logging.Logger | None = None


def _setup_syslog(tag: str = "privacyidea-radius",
                  facility: str = "auth",
                  socket: str = "") -> None:
    """Initialize syslog logger.

    Called once from instantiate() after config is loaded.
    """
    global _syslog

    fac = _SYSLOG_FACILITY_MAP.get(facility.lower(),
                                    logging.handlers.SysLogHandler.LOG_AUTH)

    # Determine syslog socket path
    if socket:
        address = socket
    elif os.path.exists("/dev/log"):
        address = "/dev/log"
    elif os.path.exists("/var/run/syslog"):
        address = "/var/run/syslog"  # macOS
    else:
        address = ("localhost", 514)

    _syslog = logging.getLogger(tag)
    _syslog.handlers.clear()
    _syslog.setLevel(logging.DEBUG)

    try:
        handler = logging.handlers.SysLogHandler(address=address, facility=fac)
        formatter = logging.Formatter(f"{tag}: [%(levelname)s] %(message)s")
        handler.setFormatter(formatter)
        _syslog.addHandler(handler)
    except Exception as e:
        # Syslog unavailable — fall back to stderr so we don't lose logs
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            f"{tag}: [%(levelname)s] %(message)s"))
        _syslog.addHandler(handler)
        _syslog.warning(f"syslog socket unavailable ({e}), falling back to stderr")


def _log(level: int, msg: str) -> None:
    """Unified logging: write to both FreeRADIUS radiusd and syslog.

    - level: one of L_DBG, L_AUTH, L_INFO, L_ERR, L_PROXY, L_ACCT
    - msg:   log message string
    """
    # FreeRADIUS internal log
    if _HAS_RADIUSD:
        try:
            radiusd.radlog(level, msg)
        except Exception:
            pass

    # Syslog
    if _syslog:
        py_level = _LEVEL_TO_PYTHON.get(level, logging.INFO)
        _syslog.log(py_level, msg)

# Service-Type values (RFC 2865 section 5.6, RFC 5765)
SERVICE_TYPE_LOGIN = 1
SERVICE_TYPE_FRAMED = 2
SERVICE_TYPE_CALLBACK_LOGIN = 3
SERVICE_TYPE_CALLBACK_FRAMED = 4
SERVICE_TYPE_OUTBOUND = 5
SERVICE_TYPE_ADMINISTRATIVE = 6
SERVICE_TYPE_NAS_PROMPT = 7
SERVICE_TYPE_AUTHENTICATE_ONLY = 8
SERVICE_TYPE_CALLBACK_NAS_PROMPT = 9
SERVICE_TYPE_CALL_CHECK = 10
SERVICE_TYPE_CALLBACK_ADMINISTRATIVE = 11
SERVICE_TYPE_AUTHORIZE_ONLY = 17

SERVICE_TYPE_NAMES = {
    1: "Login", 2: "Framed", 3: "Callback-Login", 4: "Callback-Framed",
    5: "Outbound", 6: "Administrative", 7: "NAS-Prompt",
    8: "Authenticate-Only", 9: "Callback-NAS-Prompt", 10: "Call-Check",
    11: "Callback-Administrative", 17: "Authorize-Only",
}

# ---------------------------------------------------------------------------
# Global config — populated by instantiate()
# ---------------------------------------------------------------------------
CONFIG: dict[str, Any] = {}
INI: configparser.ConfigParser | None = None
CONFIG_FILE: str = ""

DEFAULT_CONFIG = {
    "URL": "https://127.0.0.1/validate/check",
    "REALM": "",
    "RESCONF": "",
    "DEBUG": "FALSE",
    "SSL_CHECK": "FALSE",
    "SSL_CA_PATH": "",
    "TIMEOUT": "10",
    "CLIENTATTRIBUTE": "",
    "SPLIT_NULL_BYTE": "FALSE",
    "ADD_EMPTY_PASS": "FALSE",
    "SERVICE_TYPE_MODE": "permissive",  # strict | permissive
    "SYSLOG": "TRUE",                   # enable syslog output
    "SYSLOG_TAG": "privacyidea-radius", # syslog ident / program name
    "SYSLOG_FACILITY": "auth",          # syslog facility
    "SYSLOG_SOCKET": "",                # syslog socket path (auto-detect)
}

CONFIG_SEARCH_PATHS = [
    "/etc/privacyidea/rlm_python.ini",
    "/etc/privacyidea/rlm_perl.ini",
    "/etc/raddb/rlm_python.ini",
    "/etc/raddb/rlm_perl.ini",
    "/etc/freeradius/rlm_perl.ini",
    "/opt/privacyIDEA/rlm_perl.ini",
]


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(config_file_override: str = "") -> None:
    """Load INI configuration, populating global CONFIG and INI."""
    global CONFIG, INI, CONFIG_FILE

    CONFIG = dict(DEFAULT_CONFIG)

    search = [config_file_override] if config_file_override else CONFIG_SEARCH_PATHS

    for path in search:
        try:
            ini = configparser.ConfigParser()
            ini.read(path)
            if ini.sections() or ini.defaults():
                INI = ini
                CONFIG_FILE = path
                for key in DEFAULT_CONFIG:
                    val = ini.get("Default", key, fallback=None)
                    if val is not None:
                        CONFIG[key] = val
                _log(L_INFO, f"Config file {path} found!")
                return
        except Exception:
            continue

    CONFIG_FILE = "(none)"
    _log(L_INFO, "Config file not found! Using defaults.")


def _get_config_for_auth_type(auth_type: str) -> dict[str, str]:
    """Return config dict with auth-type overrides applied."""
    cfg = dict(CONFIG)
    if not INI or not auth_type:
        return cfg

    _log(L_INFO, f"Looking for config for auth-type {auth_type}")

    for key in DEFAULT_CONFIG:
        val = INI.get(auth_type, key, fallback=None)
        if val is not None:
            cfg[key] = val
            _log(L_DBG, f"Overwriting {key} to {val} based on auth-type: {auth_type}")

    return cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request_to_dict(p: tuple) -> dict[str, str]:
    """Convert FreeRADIUS request tuple to dict."""
    return dict(p)


def _decode_bytes(raw: str) -> str:
    """Try to detect encoding and decode, similar to Encode::Guess."""
    if not raw:
        return raw
    if chardet and isinstance(raw, bytes):
        detected = chardet.detect(raw)
        if detected and detected.get("encoding"):
            try:
                _log(L_INFO, f"Encoding detected: {detected['encoding']}")
                return raw.decode(detected["encoding"])
            except (UnicodeDecodeError, LookupError):
                pass
        _log(L_INFO, "Could not detect encoding. Using as-is.")
    return raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")


def _decode_hex_state(hex_state: str) -> str:
    """Decode hex-encoded State attribute, stripping leading 0x."""
    if hex_state.startswith("0x"):
        hex_state = hex_state[2:]
    return binascii.unhexlify(hex_state).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Attribute mapping
# ---------------------------------------------------------------------------

def _map_response(decoded: dict) -> dict[str, list[str]]:
    """Map privacyIDEA response to RADIUS reply attributes using INI config.

    Handles both [Mapping] and [Mapping *] group sections,
    plus [Attribute *] sections with regex matching.
    """
    rad_reply: dict[str, list[str]] = {}

    if not INI:
        return rad_reply

    detail = decoded.get("detail", {})

    # --- Process [Mapping] top-level section ---
    if INI.has_section("Mapping"):
        for key in INI.options("Mapping"):
            radius_attr = INI.get("Mapping", key)
            value = detail.get(key)
            if value is not None:
                _log(L_INFO, f"+++ Map: {key} -> {radius_attr}")
                rad_reply.setdefault(radius_attr, []).append(str(value))

    # --- Process [Mapping *] sub-sections and [Attribute *] sections ---
    for section in INI.sections():
        if section == "Default" or section == "Mapping":
            continue

        parts = section.split(None, 1)
        group = parts[0]
        member_name = parts[1] if len(parts) > 1 else ""

        if group == "Mapping" and member_name:
            # [Mapping user] — detail->{member_name}->{key} -> radius attribute
            sub = detail.get(member_name, {})
            if not isinstance(sub, dict):
                continue
            _log(L_INFO, f"++++ Parsing Mapping sub-section: {section}")
            for key in INI.options(section):
                radius_attr = INI.get(section, key)
                value = sub.get(key)
                if value is not None:
                    _log(L_INFO, f"++++++ Map: {member_name} : {key} -> {radius_attr}")
                    rad_reply.setdefault(radius_attr, []).append(str(value))

        elif group == "Attribute":
            # [Attribute Filter-Id] / [Attribute otherAttribute]
            _log(L_INFO, f"++++ Parsing Attribute section: {section}")

            radius_attr = member_name
            ra_override = INI.get(section, "radiusAttribute", fallback=None)
            if ra_override:
                radius_attr = ra_override

            user_attribute = INI.get(section, "userAttribute", fallback="")
            regex = INI.get(section, "regex", fallback="")
            directory = INI.get(section, "dir", fallback="")
            prefix = INI.get(section, "prefix", fallback="")
            suffix = INI.get(section, "suffix", fallback="")

            if not user_attribute or not regex:
                continue

            _log(L_INFO,
                           f"++++++ Attribute: IF '{directory}'->'{user_attribute}' "
                           f"== '{regex}' THEN '{radius_attr}'")

            # Get attribute value from response
            if directory:
                attr_value = detail.get(directory, {}).get(user_attribute)
                _log(L_INFO, f"++++++ searching in directory {directory}")
            else:
                attr_value = detail.get(user_attribute)
                _log(L_INFO, "++++++ no directory")

            # Normalize to list
            values: list
            if attr_value is None:
                values = []
            elif isinstance(attr_value, list):
                values = attr_value
                _log(L_INFO, f"+++++++ User attribute is a list")
            else:
                values = [attr_value]
                _log(L_INFO, f"+++++++ User attribute is a string: {attr_value}")

            for val in values:
                val_str = str(val)
                _log(L_INFO, f"+++++++ trying to match {val_str}")
                m = re.search(regex, val_str)
                if m:
                    result = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                    final = f"{prefix}{result}{suffix}"
                    rad_reply.setdefault(radius_attr, []).append(final)
                    _log(L_INFO,
                                   f"++++++++ Result: Add RADIUS attribute {radius_attr} = {final}")
                else:
                    _log(L_INFO,
                                   f"++++++++ Result: No match, no RADIUS attribute {radius_attr} added.")

    return rad_reply


# ---------------------------------------------------------------------------
# privacyIDEA HTTP client
# ---------------------------------------------------------------------------

def _call_privacyidea(url: str, params: dict, cfg: dict) -> requests.Response:
    """POST to privacyIDEA /validate/check."""
    ssl_check = cfg.get("SSL_CHECK", "FALSE").upper() == "TRUE"
    ssl_ca_path = cfg.get("SSL_CA_PATH", "")
    timeout = int(cfg.get("TIMEOUT", 10))

    verify: bool | str
    if ssl_check:
        _log(L_INFO, "Verifying SSL certificate!")
        if ssl_ca_path:
            _log(L_INFO, f"SSL_CA_PATH: {ssl_ca_path}")
            verify = ssl_ca_path
        else:
            _log(L_INFO, "Verifying SSL certificate against system wide CAs!")
            verify = True
    else:
        _log(L_INFO, "Not verifying SSL certificate!")
        verify = False

    session = requests.Session()
    session.headers["User-Agent"] = "FreeRADIUS"

    _log(L_INFO, f"Request timeout: {timeout}")
    start = time.time()
    response = session.post(url, data=params, verify=verify, timeout=timeout)
    elapsed = time.time() - start
    _log(L_INFO, f"elapsed time for privacyidea call: {elapsed:.6f}")

    return response


# ---------------------------------------------------------------------------
# FreeRADIUS rlm_python3 interface
# ---------------------------------------------------------------------------

def instantiate(p: tuple) -> int:
    """Called once when FreeRADIUS loads the module."""
    conf = dict(p)
    config_file = conf.get("configfile", "")

    # Load INI config first (populates global CONFIG)
    _load_config(config_file)

    # Initialize syslog based on loaded config
    if CONFIG.get("SYSLOG", "TRUE").upper() == "TRUE":
        _setup_syslog(
            tag=CONFIG.get("SYSLOG_TAG", "privacyidea-radius"),
            facility=CONFIG.get("SYSLOG_FACILITY", "auth"),
            socket=CONFIG.get("SYSLOG_SOCKET", ""),
        )

    _log(L_INFO, f"rlm_python3 privacyIDEA instantiate, configfile={config_file}")
    _log(L_INFO, f"Config file {CONFIG_FILE}")
    _log(L_INFO, f"syslog enabled, tag={CONFIG.get('SYSLOG_TAG')}, "
                 f"facility={CONFIG.get('SYSLOG_FACILITY')}")
    return 0


def _get_service_type(rad_request: dict) -> int:
    """Extract Service-Type from request, return 0 if absent."""
    raw = rad_request.get("Service-Type", "0")
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0


def _should_map_attributes(service_type: int, cfg: dict) -> bool:
    """Decide whether to include mapped attributes in the reply.

    strict mode:
        Service-Type=8  (Authenticate Only) -> no mapping (RFC 2865)
        Service-Type=17 (Authorize Only)    -> mapping only, no auth
        all others                          -> full auth + mapping
    permissive mode:
        always map (current behavior, works with most NAS devices)
    """
    mode = cfg.get("SERVICE_TYPE_MODE", "permissive").lower()
    if mode != "strict":
        return True
    return service_type != SERVICE_TYPE_AUTHENTICATE_ONLY


def _build_params(rad_request: dict, cfg: dict) -> dict[str, str]:
    """Extract privacyIDEA request parameters from RADIUS request."""
    params: dict[str, str] = {}

    # State (challenge-response continuation)
    if "State" in rad_request:
        params["state"] = _decode_hex_state(rad_request["State"])

    # Username: prefer Stripped-User-Name over User-Name
    if "User-Name" in rad_request:
        params["user"] = rad_request["User-Name"]
    if "Stripped-User-Name" in rad_request:
        params["user"] = rad_request["Stripped-User-Name"]

    # Password
    if "User-Password" in rad_request:
        password = rad_request["User-Password"]
        if cfg.get("SPLIT_NULL_BYTE", "FALSE").upper() == "TRUE":
            password = password.split("\x00")[0]
        password = _decode_bytes(password)
        params["pass"] = password
    elif cfg.get("ADD_EMPTY_PASS", "FALSE").upper() == "TRUE":
        params["pass"] = ""

    # Decode username encoding
    if "user" in params:
        params["user"] = _decode_bytes(params["user"])

    # Client IP: NAS-IP-Address -> Packet-Src-IP-Address -> CLIENTATTRIBUTE
    if "NAS-IP-Address" in rad_request:
        params["client"] = rad_request["NAS-IP-Address"]
        _log(L_INFO, f"Setting client IP to {params['client']}.")
    elif "Packet-Src-IP-Address" in rad_request:
        params["client"] = rad_request["Packet-Src-IP-Address"]
        _log(L_INFO, f"Setting client IP to {params['client']}.")

    client_attr = cfg.get("CLIENTATTRIBUTE", "")
    if client_attr and client_attr in rad_request:
        params["client"] = rad_request[client_attr]
        _log(L_INFO, f"Setting client IP to {params['client']}.")

    # Realm
    realm = cfg.get("REALM", "")
    if realm:
        params["realm"] = realm
    elif rad_request.get("Realm"):
        params["realm"] = rad_request["Realm"]

    # Resolver
    resconf = cfg.get("RESCONF", "")
    if resconf:
        params["resConf"] = resconf

    return params


def _handle_authorize_only(rad_request: dict, cfg: dict) -> tuple:
    """Handle Service-Type=17 (Authorize Only).

    The user is already authenticated by another mechanism. We only need
    to return authorization attributes (Framed-IP-Address, Filter-Id, etc.)
    without calling privacyIDEA /validate/check.

    Since we have no PI response to map from, we accept the request and
    let FreeRADIUS use attributes from other modules (rlm_ldap, files, etc.).
    """
    user = rad_request.get("Stripped-User-Name",
                           rad_request.get("User-Name", ""))
    _log(L_INFO,
                   f"Service-Type=Authorize-Only for user '{user}' — "
                   "skipping privacyIDEA authentication")

    reply_pairs: list[tuple[str, str]] = []
    config_pairs: list[tuple[str, str]] = []

    # Echo Message-Authenticator
    if "Message-Authenticator" in rad_request:
        reply_pairs.append(("Message-Authenticator",
                            rad_request["Message-Authenticator"]))

    reply_pairs.append(("Reply-Message", "privacyIDEA authorize only — accepted"))

    _log(L_INFO, f"return {RET_NAMES[RLM_MODULE_OK]}")
    return (RLM_MODULE_OK, tuple(reply_pairs), tuple(config_pairs))


def _handle_pi_response(response: requests.Response, params: dict,
                        cfg: dict, service_type: int,
                        reply_pairs: list, config_pairs: list,
                        reply_message_idx: int, debug: bool) -> int:
    """Parse privacyIDEA JSON response and determine RADIUS return code."""
    content = response.text
    g_return = RLM_MODULE_REJECT

    if debug:
        _log(L_DBG, f"Content {content}")

    if not response.ok:
        status_line = f"{response.status_code} {response.reason}"
        _log(L_INFO, f"privacyIDEA request failed: {status_line}")
        reply_pairs[reply_message_idx] = (
            "Reply-Message", f"privacyIDEA request failed: {status_line}")
        return RLM_MODULE_FAIL

    do_mapping = _should_map_attributes(service_type, cfg)

    try:
        decoded = response.json()
        message = decoded.get("detail", {}).get("message", "")

        if decoded.get("result", {}).get("value"):
            # Authentication successful
            user = params.get("user", "")
            realm_val = params.get("realm", "")
            _log(L_INFO,
                           f"privacyIDEA access granted for {user} realm='{realm_val}'")
            reply_pairs[reply_message_idx] = (
                "Reply-Message", "privacyIDEA access granted")

            if do_mapping:
                for attr, values in _map_response(decoded).items():
                    for v in values:
                        reply_pairs.append((attr, v))
            else:
                _log(L_INFO,
                               "Service-Type=Authenticate-Only, strict mode — "
                               "skipping attribute mapping")

            g_return = RLM_MODULE_OK

        elif decoded.get("result", {}).get("status"):
            _log(L_INFO, "privacyIDEA Result status is true!")
            reply_pairs[reply_message_idx] = ("Reply-Message", message)

            transaction_id = decoded.get("detail", {}).get("transaction_id")
            if transaction_id:
                # Challenge-response mode
                reply_pairs.append(("State", transaction_id))
                config_pairs.append(("Response-Packet-Type", "Access-Challenge"))

                if do_mapping:
                    for attr, values in _map_response(decoded).items():
                        for v in values:
                            reply_pairs.append((attr, v))

                g_return = RLM_MODULE_HANDLED
            else:
                user = params.get("user", "")
                realm_val = params.get("realm", "")
                _log(L_INFO,
                               f"privacyIDEA access denied for {user} realm='{realm_val}'")
                g_return = RLM_MODULE_REJECT

        elif not decoded.get("result", {}).get("status"):
            # Internal error
            _log(L_INFO, "privacyIDEA Result status is false!")
            error_msg = decoded.get("result", {}).get("error", {}).get("message", "")
            reply_pairs[reply_message_idx] = ("Reply-Message", error_msg)
            _log(L_INFO, error_msg)

            error_code = decoded.get("result", {}).get("error", {}).get("code", 0)
            if error_code == 904:
                g_return = RLM_MODULE_NOTFOUND
            else:
                g_return = RLM_MODULE_FAIL

            _log(L_INFO, "privacyIDEA failed to handle the request")

    except Exception as e:
        _log(L_INFO, str(e))
        _log(L_INFO, "Can not parse response from privacyIDEA.")

    return g_return


def authenticate(p: tuple) -> tuple:
    """Main authentication handler — called by FreeRADIUS."""
    rad_request = _request_to_dict(p)
    reply_pairs: list[tuple[str, str]] = []
    config_pairs: list[tuple[str, str]] = []

    # Log config origin
    _log(L_INFO, f"Config File {CONFIG_FILE}")

    # Get auth-type specific config
    auth_type = rad_request.get("Auth-Type", "")
    cfg = _get_config_for_auth_type(auth_type)

    url = cfg["URL"]
    debug = cfg.get("DEBUG", "FALSE").upper() == "TRUE"
    mode = cfg.get("SERVICE_TYPE_MODE", "permissive").lower()

    # --- Service-Type handling ---
    service_type = _get_service_type(rad_request)
    st_name = SERVICE_TYPE_NAMES.get(service_type, str(service_type))
    _log(L_INFO, f"Service-Type: {st_name} ({service_type})")
    _log(L_INFO, f"SERVICE_TYPE_MODE: {mode}")

    # Service-Type=17 (Authorize Only) in strict mode:
    # skip authentication entirely, user is already authenticated
    if service_type == SERVICE_TYPE_AUTHORIZE_ONLY and mode == "strict":
        return _handle_authorize_only(rad_request, cfg)

    _log(L_INFO, f"Debugging config: {cfg.get('DEBUG', 'FALSE')}")
    _log(L_INFO, f"Verifying SSL certificate: {cfg.get('SSL_CHECK', 'FALSE')}")
    _log(L_INFO, f"Default URL {url}")

    if debug:
        for k, v in rad_request.items():
            _log(L_DBG, f"RAD_REQUEST: {k} = {v}")

    # --- Build privacyIDEA request params ---
    params = _build_params(rad_request, cfg)

    # Echo Message-Authenticator
    if "Message-Authenticator" in rad_request:
        reply_pairs.append(("Message-Authenticator",
                            rad_request["Message-Authenticator"]))

    # Logging
    _log(L_INFO, f"Auth-Type: {auth_type}")
    _log(L_INFO, f"url: {url}")
    _log(L_INFO, f"user sent to privacyidea: {params.get('user', '')}")
    _log(L_INFO, f"realm sent to privacyidea: {params.get('realm', '')}")
    _log(L_INFO, f"resolver sent to privacyidea: {params.get('resConf', '')}")
    _log(L_INFO, f"client sent to privacyidea: {params.get('client', '')}")
    _log(L_INFO, f"state sent to privacyidea: {params.get('state', '')}")

    if debug:
        for k, v in params.items():
            _log(L_DBG, f"urlparam {k} = {v}")
    else:
        for k in params:
            _log(L_INFO, f"urlparam {k}")

    # --- Default: reject ---
    reply_pairs.append(("Reply-Message", "privacyIDEA server denied access!"))
    reply_message_idx = len(reply_pairs) - 1

    # --- Call privacyIDEA ---
    try:
        response = _call_privacyidea(url, params, cfg)
    except Exception as e:
        _log(L_INFO, f"privacyIDEA request failed: {e}")
        reply_pairs[reply_message_idx] = (
            "Reply-Message", f"privacyIDEA request failed: {e}")
        g_return = RLM_MODULE_FAIL
        _log(L_INFO, f"return {RET_NAMES.get(g_return, g_return)}")
        return (g_return, tuple(reply_pairs), tuple(config_pairs))

    # --- Parse response (Service-Type aware) ---
    g_return = _handle_pi_response(
        response, params, cfg, service_type,
        reply_pairs, config_pairs, reply_message_idx, debug)

    _log(L_INFO, f"return {RET_NAMES.get(g_return, g_return)}")
    return (g_return, tuple(reply_pairs), tuple(config_pairs))


def authorize(p: tuple) -> tuple:
    return (RLM_MODULE_OK, (), ())


def preacct(p: tuple) -> tuple:
    return (RLM_MODULE_OK, (), ())


def accounting(p: tuple) -> tuple:
    return (RLM_MODULE_OK, (), ())


def checksimul(p: tuple) -> tuple:
    return (RLM_MODULE_OK, (), ())


def pre_proxy(p: tuple) -> tuple:
    return (RLM_MODULE_OK, (), ())


def post_proxy(p: tuple) -> tuple:
    return (RLM_MODULE_OK, (), ())


def post_auth(p: tuple) -> tuple:
    return (RLM_MODULE_OK, (), ())


def detach(_p=None) -> int:
    _log(L_INFO, "rlm_python3::Detaching. Reloading. Done.")
    return 0
