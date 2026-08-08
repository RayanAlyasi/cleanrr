FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# hatch-vcs reads the version from git, which isn't in the build context.
# Release CI passes the real version via --build-arg; local builds get a
# placeholder so the package metadata still resolves.
ARG VERSION=0.0.0+local
ENV SETUPTOOLS_SCM_PRETEND_VERSION=$VERSION

COPY pyproject.toml LICENSE README.md ./
COPY cleanrr ./cleanrr
# pip isn't needed at runtime; drop it (and its vendored deps, e.g. msgpack)
# so scanners don't flag CVEs in tooling that's never imported by cleanrr.
RUN pip install . \
 && pip uninstall -y pip setuptools wheel

RUN useradd --create-home --shell /bin/bash --uid 1000 cleanrr \
 && mkdir -p /app/data \
 && chown -R cleanrr:cleanrr /app
USER cleanrr

CMD ["python", "-m", "cleanrr"]
