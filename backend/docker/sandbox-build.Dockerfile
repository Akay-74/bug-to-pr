# Pre-baked sandbox base image: a slim Python image plus the system build
# tools some old benchmark repos need to compile C/Cython extensions
# (e.g. astropy, scikit-learn `pip install -e .`).
#
# Built once per Python version and reused across every candidate that
# needs that version, instead of installing a compiler on every validation
# run (impossible anyway: the runtime sandbox drops ALL capabilities, so
# apt-get cannot run inside it) or pulling a much larger full `python:X`
# image just to get gcc.
#
# This Dockerfile only affects build-time image construction. The runtime
# sandbox (backend/tools/sandbox.py) still runs containers from the
# resulting image as non-root (UID 65534), with cap_drop=ALL,
# no-new-privileges, and network disabled during test execution -- none of
# that changes.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG APT_PACKAGES="build-essential"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ${APT_PACKAGES} \
    && rm -rf /var/lib/apt/lists/*

# Only python:3.6-slim needs this: its `ensurepip` module bundles pip 18.1,
# which every fresh `python -m venv` in the sandbox bootstraps -- and 18.1
# predates the `--no-use-pep517`/`--no-build-isolation` flags some benchmark
# install commands require. Swapping the bundled wheel makes `python -m venv`
# create venvs with a modern pip, without touching anything at runtime.
ARG PIP_BOOTSTRAP_VERSION=""

RUN if [ -n "${PIP_BOOTSTRAP_VERSION}" ]; then \
        PYVER=$(python3 -c "import sys; print('%d.%d' % sys.version_info[:2])") && \
        ENSUREPIP_DIR="/usr/local/lib/python${PYVER}/ensurepip" && \
        OLD_VERSION=$(python3 -c "import ensurepip; print(ensurepip._PIP_VERSION)") && \
        mkdir -p /tmp/pipwhl && \
        pip download --no-deps -d /tmp/pipwhl "pip==${PIP_BOOTSTRAP_VERSION}" && \
        cp /tmp/pipwhl/pip-*.whl "${ENSUREPIP_DIR}/_bundled/pip-${PIP_BOOTSTRAP_VERSION}-py2.py3-none-any.whl" && \
        rm -f "${ENSUREPIP_DIR}/_bundled/pip-${OLD_VERSION}-py2.py3-none-any.whl" && \
        sed -i "s/_PIP_VERSION = \"${OLD_VERSION}\"/_PIP_VERSION = \"${PIP_BOOTSTRAP_VERSION}\"/" "${ENSUREPIP_DIR}/__init__.py" && \
        rm -rf /tmp/pipwhl; \
    fi
