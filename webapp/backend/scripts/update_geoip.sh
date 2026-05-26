#!/bin/bash
# Refresh the offline MaxMind GeoLite2-City database used by main.py for
# IP→country/city lookups on usage_events.
#
# Driven by webapp/urantia-library-geoip.timer (monthly). Also runnable by
# hand for the one-time seed:
#     set -a; . /Books/urantia-library/webapp/secrets.env; set +a
#     /Books/urantia-library/webapp/backend/scripts/update_geoip.sh
#
# Requires MAXMIND_ACCOUNT_ID and MAXMIND_LICENSE_KEY in the environment
# (the systemd unit reads them from secrets.env). Sign up free at
# https://www.maxmind.com/en/geolite2/signup and generate a license key.
#
# The reader in main.py is opened once at startup, so a fresh .mmdb only
# takes effect on the next service restart. That's fine for monthly cadence.

set -euo pipefail

: "${MAXMIND_ACCOUNT_ID:?MAXMIND_ACCOUNT_ID not set (see secrets.env)}"
: "${MAXMIND_LICENSE_KEY:?MAXMIND_LICENSE_KEY not set (see secrets.env)}"

DATA_DIR="${BOOKS_DIR:-/Books}/.data"
GEOIP_DIR="$DATA_DIR/geoip"
DEST="$GEOIP_DIR/GeoLite2-City.mmdb"

mkdir -p "$GEOIP_DIR"

tmp_tarball="$(mktemp --suffix=.tar.gz)"
tmp_extract="$(mktemp -d)"
trap 'rm -rf "$tmp_tarball" "$tmp_extract"' EXIT

url="https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-City&license_key=${MAXMIND_LICENSE_KEY}&suffix=tar.gz"

echo "update_geoip: downloading GeoLite2-City.tar.gz ..."
curl -fsSL -o "$tmp_tarball" "$url"

echo "update_geoip: extracting ..."
tar -xzf "$tmp_tarball" -C "$tmp_extract"

# Tarball contains GeoLite2-City_YYYYMMDD/GeoLite2-City.mmdb — find it.
src_mmdb="$(find "$tmp_extract" -type f -name 'GeoLite2-City.mmdb' | head -n1)"
if [[ -z "$src_mmdb" ]]; then
    echo "update_geoip: GeoLite2-City.mmdb not found in tarball" >&2
    exit 1
fi

# Atomic rename so a half-written file never replaces a working one.
mv -f "$src_mmdb" "${DEST}.new"
mv -f "${DEST}.new" "$DEST"

echo "update_geoip: wrote $DEST"
echo "update_geoip: NOTE — restart urantia-library.service to pick up the new database (the reader is opened at startup)."
