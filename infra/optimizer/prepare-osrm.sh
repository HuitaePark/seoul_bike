#!/bin/sh
# Run only while the local osrm service is stopped. No downloads or server SSH.
set -eu
test -s /input/region.osm.pbf || {
    echo 'Place a local OSM extract at infra/optimizer/data/input/region.osm.pbf first.' >&2
    exit 1
}
mkdir -p /data
version=$(
    { sha256sum /input/region.osm.pbf /opt/car.lua; printf '%s\n' 'osrm-v26.10.0:ch:car'; } |
    sha256sum | cut -d ' ' -f 1
)
if [ -s /data/data-version ] && [ "$(cat /data/data-version)" = "$version" ] && [ -s /data/region.osrm.hsgr ]; then
    echo "Prepared road data already matches $version"
    exit 0
fi
stage=$(mktemp -d /data/.prepare.XXXXXX)
trap 'rm -rf "$stage"' EXIT HUP INT TERM
cp /input/region.osm.pbf "$stage/region.osm.pbf"
osrm-extract -p /opt/car.lua "$stage/region.osm.pbf"
osrm-contract "$stage/region.osrm"
# Remove readiness before replacing artifacts. A failed replacement cannot be
# mistaken for a completed preparation by the routed service entrypoint.
rm -f /data/data-version
rm -f /data/region.osrm*
for artifact in "$stage"/region.osrm*; do
    mv "$artifact" /data/
done
printf '%s\n' "$version" > /data/data-version
echo "Prepared CH road data: $version"
