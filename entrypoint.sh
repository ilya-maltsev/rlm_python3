#!/bin/sh
set -e

PATH=/opt/sbin:/opt/bin:$PATH
export PATH

### Configure privacyIDEA connection from environment variables
sed -i  -e "s|^URL =.*|URL = ${RADIUS_PI_HOST:-http://127.0.0.1}/validate/check|" \
        -e "s|^REALM =.*|REALM = ${RADIUS_PI_REALM}|" \
        -e "s|^RESCONF =.*|RESCONF = ${RADIUS_PI_RESCONF}|" \
        -e "s|^SSL_CHECK =.*|SSL_CHECK = ${RADIUS_PI_SSLCHECK:-false}|" \
        -e "s|^DEBUG =.*|DEBUG = ${RADIUS_DEBUG:-false}|" \
        -e "s|^TIMEOUT =.*|TIMEOUT = ${RADIUS_PI_TIMEOUT:-10}|" \
        -e "s|^SYSLOG =.*|SYSLOG = ${RADIUS_SYSLOG:-true}|" \
        -e "s|^SYSLOG_TAG =.*|SYSLOG_TAG = ${RADIUS_SYSLOG_TAG:-privacyidea-radius}|" \
        -e "s|^SYSLOG_FACILITY =.*|SYSLOG_FACILITY = ${RADIUS_SYSLOG_FACILITY:-auth}|" \
        -e "s|^SYSLOG_HOST =.*|SYSLOG_HOST = ${RADIUS_SYSLOG_HOST}|" \
        -e "s|^SYSLOG_PORT =.*|SYSLOG_PORT = ${RADIUS_SYSLOG_PORT:-514}|" \
        -e "s|^SYSLOG_PROTO =.*|SYSLOG_PROTO = ${RADIUS_SYSLOG_PROTO:-udp}|" \
        -e "s|^SYSLOG_LEVEL =.*|SYSLOG_LEVEL = ${RADIUS_SYSLOG_LEVEL:-INFO}|" /etc/raddb/rlm_python.ini

### Configure [Mapping user] from RADIUS_PI_MAPPING_USER
# Format: comma-separated "pi_key=Radius-Attribute" pairs.
# Example: RADIUS_PI_MAPPING_USER="vpn_ip=Framed-IP-Address,group=Class"
# Each pair maps privacyIDEA detail.user.<pi_key> to the named RADIUS reply
# attribute. The block is regenerated on every start, so changing the env var
# and restarting the container reliably updates the mapping.
INI_FILE=/etc/raddb/rlm_python.ini
BEGIN_MARK='# BEGIN auto-mapping-user'
END_MARK='# END auto-mapping-user'

# 1) Drop any previous auto-block so restarts stay idempotent.
sed -i "/^${BEGIN_MARK}$/,/^${END_MARK}$/d" "$INI_FILE"

# 2) If env var is set, inject a fresh block right after [Mapping user].
if [ -n "${RADIUS_PI_MAPPING_USER:-}" ]; then
    TMP_INI=$(mktemp)
    awk -v pairs="$RADIUS_PI_MAPPING_USER" \
        -v begin="$BEGIN_MARK" -v end="$END_MARK" '
        {
            print
            if ($0 ~ /^\[Mapping user\][[:space:]]*$/) {
                print begin
                n = split(pairs, parts, ",")
                for (i = 1; i <= n; i++) {
                    p = parts[i]
                    sub(/^[[:space:]]+/, "", p); sub(/[[:space:]]+$/, "", p)
                    if (p == "") continue
                    eq = index(p, "=")
                    if (eq < 2) continue
                    k = substr(p, 1, eq - 1)
                    v = substr(p, eq + 1)
                    sub(/[[:space:]]+$/, "", k); sub(/^[[:space:]]+/, "", v)
                    if (k == "" || v == "") continue
                    printf "%s = %s\n", k, v
                }
                print end
            }
        }
    ' "$INI_FILE" > "$TMP_INI" && mv "$TMP_INI" "$INI_FILE"
fi
### end of configure

### Start syslogd for plugin logging
# busybox syslogd writes to /var/log/messages and creates /dev/log socket.
# Use -n (foreground) with -O- to also log to stdout for `docker logs`.
# If syslogd is not available, the plugin falls back to stderr.
if command -v syslogd >/dev/null 2>&1; then
    syslogd -n -O /dev/stdout -S &
fi

# this if will check if the first argument is a flag
# but only works if all arguments require a hyphenated flag
# -v; -SL; -f arg; etc will work, but not arg1 arg2
if [ "$#" -eq 0 ] || [ "${1#-}" != "$1" ]; then
    set -- radiusd "$@"
fi

# check for the expected command
if [ "$1" = 'radiusd' ]; then
    shift
    exec radiusd -f "$@"
fi

# debian people are likely to call "freeradius" as well, so allow that
if [ "$1" = 'freeradius' ]; then
    shift
    exec radiusd -f "$@"
fi

# else default to run whatever the user wanted like "bash" or "sh"
exec "$@"
