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
