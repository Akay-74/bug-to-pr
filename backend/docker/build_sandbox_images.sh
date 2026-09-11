#!/usr/bin/env bash
# Build the pre-baked sandbox images referenced by benchmark metadata's
# `docker_image` field. Run this once (and again only if a new Python
# version / package set is needed); validation runs then reuse the cached
# result instead of installing build tools per-run.
#
# All base images (python:3.6-slim, python:3.8-slim, python:3.9-slim) are
# already present on this host, so these builds need no image pulls -- only
# a one-time, cached `apt-get install` per version.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

build() {
    local tag="$1" base="$2" packages="$3" pip_bootstrap="${4:-}"
    echo "=== building ${tag} (from ${base}) ==="
    docker build \
        --build-arg "BASE_IMAGE=${base}" \
        --build-arg "APT_PACKAGES=${packages}" \
        --build-arg "PIP_BOOTSTRAP_VERSION=${pip_bootstrap}" \
        -t "${tag}" \
        -f sandbox-build.Dockerfile \
        .
}

# python:3.9 -- astropy-14508, astropy-14539 (Cython/C build via `pip install -e .`).
# git is needed because astropy's build derives its version with
# setuptools-scm, which shells out to `git describe` at build time.
build bug2pr-sandbox:py39-build python:3.9-slim "build-essential git"

# python:3.6 -- scikit-learn-13779, scikit-learn-14053 (Cython build,
# --no-use-pep517 --no-build-isolation). The matplotlib/pandas install pulls
# in Pillow; a fresh py3.6 venv only has the ensurepip-bundled pip (18.1),
# which doesn't understand manylinux2014 wheel tags, so it falls back to
# Pillow's sdist and builds from source. zlib1g-dev and libjpeg-dev cover
# Pillow's two hard build requirements (zlib, jpeg) for that source build.
# Bundled pip 18.1 also predates --no-use-pep517/--no-build-isolation, which
# both issues' install commands need, so the ensurepip wheel is swapped for
# pip 21.3.1 (the last release supporting Python 3.6) too.
build bug2pr-sandbox:py36-build python:3.6-slim "build-essential zlib1g-dev libjpeg-dev" "21.3.1"

# python:3.8 -- matplotlib-20676 (may build from source; freetype/png headers
# cover matplotlib's native backend deps if no wheel matches). git is needed
# because dev-requirements.txt installs sphinx-gallery from a git+ URL.
build bug2pr-sandbox:py38-build python:3.8-slim "build-essential pkg-config libfreetype6-dev libpng-dev git"

echo "=== done ==="
docker images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | grep '^bug2pr-sandbox:'
