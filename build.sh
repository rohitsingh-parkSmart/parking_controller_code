#!/usr/bin/env bash
#
# Build (and optionally push) the ParkSmart controller image.
#
# The deploy target is a Raspberry Pi, but builds normally happen on
# an x86_64 dev machine, so this uses buildx to cross-build for arm.
# Runs on Linux/macOS and in Git Bash on Windows.
#
#   ./build.sh build          local image for THIS machine's arch,
#                             loaded into the local docker (for a
#                             quick "does it even start" check)
#   ./build.sh push           multi-arch (arm64 + armv7) build pushed
#                             to the registry, which is what the Pi
#                             and Watchtower actually consume
#   ./build.sh print          show the resolved tags and exit
#
# Override anything via the environment:
#   REGISTRY      default ghcr.io
#   IMAGE_OWNER   default rohitsingh-parksmart   (must be lowercase)
#   IMAGE_NAME    default parksmart
#   VERSION       default rpi-v<YYYYMMDD>
#   PLATFORMS     default linux/arm64,linux/arm/v7
#   ROLLING_TAG   default rpi-latest             (empty to skip)

set -euo pipefail

cd "$(dirname "$0")"

REGISTRY="${REGISTRY:-ghcr.io}"
IMAGE_OWNER="${IMAGE_OWNER:-rohitsingh-parksmart}"
IMAGE_NAME="${IMAGE_NAME:-parksmart}"

# Registry paths must be lowercase; the GitHub owner is mixed-case
# (rohitsingh-parkSmart), so normalise rather than fail at push time
# with an opaque "invalid reference format".
REPO="$(printf '%s/%s/%s' "$REGISTRY" "$IMAGE_OWNER" "$IMAGE_NAME" | tr '[:upper:]' '[:lower:]')"

VERSION="${VERSION:-rpi-v$(date +%Y%m%d)}"
PLATFORMS="${PLATFORMS:-linux/arm64,linux/arm/v7}"

# Watchtower updates a container when the digest behind its CURRENT
# tag changes. A container pinned to an immutable dated tag therefore
# never auto-updates, so we also publish a rolling tag for compose to
# track. The dated tag stays as the immutable record of a release.
ROLLING_TAG="${ROLLING_TAG-rpi-latest}"

VCS_REF="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if ! git diff --quiet HEAD -- main.py devices requirements.txt Dockerfile 2>/dev/null; then
    VCS_REF="${VCS_REF}-dirty"
fi

# A named docker-container builder is what reliably supports building
# several platforms into one manifest list and pushing it. The
# default "docker" driver cannot do that on every Docker version.
BUILDER="${BUILDER:-parksmart}"

ensure_builder() {
    if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
        echo ">> creating buildx builder '$BUILDER' (docker-container driver)"
        docker buildx create --name "$BUILDER" \
            --driver docker-container --bootstrap >/dev/null
    fi
}

build_args() {
    printf '%s\n' \
        --build-arg "VERSION=${VERSION}" \
        --build-arg "VCS_REF=${VCS_REF}" \
        --build-arg "BUILD_DATE=${BUILD_DATE}"
}

tag_args() {
    printf '%s\n' --tag "${REPO}:${VERSION}"
    if [ -n "$ROLLING_TAG" ]; then
        printf '%s\n' --tag "${REPO}:${ROLLING_TAG}"
    fi
}

show_plan() {
    echo "repo      : ${REPO}"
    echo "version   : ${VERSION}"
    echo "rolling   : ${ROLLING_TAG:-<none>}"
    echo "revision  : ${VCS_REF}"
    echo "built at  : ${BUILD_DATE}"
}

cmd="${1:-build}"

case "$cmd" in

    print)
        show_plan
        echo "platforms : ${PLATFORMS}  (push)"
        ;;

    build)
        # --load can only ever materialise a single platform into the
        # local image store, so a local smoke-test build is this
        # machine's arch only. Cross-arch verification happens on the
        # Pi against the pushed manifest.
        show_plan
        echo "platforms : <this machine>  (load)"
        ensure_builder
        set -x
        docker buildx build \
            --builder "$BUILDER" \
            $(build_args) \
            --tag "${REPO}:${VERSION}" \
            --load \
            .
        ;;

    push)
        show_plan
        echo "platforms : ${PLATFORMS}  (push)"

        if [ "${VCS_REF}" != "${VCS_REF%-dirty}" ]; then
            echo "WARNING: working tree has uncommitted changes to app" \
                 "files; pushing anyway as ${VCS_REF}" >&2
        fi

        # Cheap heads-up so a missing credential shows up now rather
        # than after several minutes of emulated arm build. This is
        # only a heuristic — a credential helper (credsStore) may
        # hold the entry outside config.json — so it warns and
        # continues instead of blocking a legitimate push. If it is
        # genuinely missing, buildx fails at the push step with its
        # own unauthorized error.
        if ! grep -q "$REGISTRY" "${HOME}/.docker/config.json" 2>/dev/null; then
            echo "NOTE: no ${REGISTRY} entry found in ~/.docker/config.json." >&2
            echo "  If the push fails as unauthorized, run:" >&2
            echo "  echo \$GHCR_TOKEN | docker login ${REGISTRY}" \
                 "-u <github-username> --password-stdin" >&2
            echo "  (token needs the write:packages scope)" >&2
        fi

        ensure_builder
        set -x
        docker buildx build \
            --builder "$BUILDER" \
            --platform "$PLATFORMS" \
            $(build_args) \
            $(tag_args) \
            --push \
            .
        ;;

    *)
        echo "usage: $0 [build|push|print]" >&2
        exit 2
        ;;
esac
