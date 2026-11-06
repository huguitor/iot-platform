#!/bin/sh
# Install a reviewed root-owned copy in renewal-hooks/deploy only after approval.
set -eu
case "${RENEWED_LINEAGE-}" in
    /etc/letsencrypt/live/iot.panozoelectronica.com.ar)
        exec /usr/bin/python3 /usr/local/libexec/iot-mqtt-renew-tls.py --reload
        ;;
    *) exit 0 ;;
esac
