#!/bin/bash
#
# Build, export and import the Docker image for privacyIDEA-freeradius.
#
# Usage:
#   bash build-images.sh           # build (default)
#   bash build-images.sh build     # same as above
#   bash build-images.sh export    # export to privacyidea-freeradius-image.tar.gz
#   bash build-images.sh import    # import from privacyidea-freeradius-image.tar.gz
#   bash build-images.sh all       # build + export
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARCHIVE="${SCRIPT_DIR}/privacyidea-freeradius-image.tar.gz"
IMAGES="privacyidea-freeradius:latest"

# Whitelist: files/dirs copied into the Docker build context.
# Add new app files/dirs here; anything outside this list (docs, compose
# files, .git, etc.) stays out.
APP_FILES=(
    entrypoint.sh
    privacyidea_radius.py
    dictionary.netknights
    raddb
)

_STAGED_DIRS=()
_cleanup_staged() {
    local d
    for d in "${_STAGED_DIRS[@]}"; do
        [ -d "${d}" ] && rm -rf "${d}"
    done
}
trap _cleanup_staged EXIT

stage_build_context() {
    local src="$1"; shift
    local dir
    dir="$(mktemp -d -t privacyidea-freeradius-ctx.XXXXXX)"
    _STAGED_DIRS+=("${dir}")
    local item
    for item in "$@"; do
        if [ ! -e "${src}/${item}" ]; then
            echo "ERROR: required file '${item}' not found in ${src}" >&2
            exit 1
        fi
        cp -a "${src}/${item}" "${dir}/"
    done
    echo "${dir}"
}

build_images() {
    echo "=== Building privacyidea-freeradius:latest ==="
    local ctx
    ctx="$(stage_build_context "${SCRIPT_DIR}" "${APP_FILES[@]}")"
    docker build -f "${SCRIPT_DIR}/Dockerfile" -t privacyidea-freeradius:latest "${ctx}"

    echo ""
    echo "=== Images built ==="
    docker images --format "  {{.Repository}}:{{.Tag}}  {{.Size}}" | grep -E "^  privacyidea-freeradius"
}

export_images() {
    echo "=== Exporting images to ${ARCHIVE} ==="
    docker save ${IMAGES} | gzip > "${ARCHIVE}"
    echo "  $(du -h "${ARCHIVE}" | cut -f1)  ${ARCHIVE}"
    echo "=== Export done ==="
}

import_images() {
    if [ ! -f "${ARCHIVE}" ]; then
        echo "ERROR: ${ARCHIVE} not found."
        echo "Run '$(basename "$0") export' first or copy the archive here."
        exit 1
    fi
    echo "=== Importing images from ${ARCHIVE} ==="
    gunzip -c "${ARCHIVE}" | docker load
    echo ""
    echo "=== Images loaded ==="
    docker images --format "  {{.Repository}}:{{.Tag}}  {{.Size}}" | grep -E "^  privacyidea-freeradius"
    echo ""
    echo "Now run:  docker compose up -d"
}

CMD="${1:-build}"

case "${CMD}" in
    build)  build_images ;;
    export) export_images ;;
    import) import_images ;;
    all)    build_images; echo ""; export_images ;;
    *)
        echo "Usage: $(basename "$0") {build|export|import|all}"
        exit 1
        ;;
esac
