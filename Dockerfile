FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# hatch-vcs reads the version from git, which isn't in the build context.
# Release CI passes the real version via --build-arg; local builds get a
# placeholder so the package metadata still resolves.
ARG VERSION=0.0.0+local
ENV HATCH_VCS_PRETEND_VERSION=$VERSION

COPY pyproject.toml ./
COPY cleanrr ./cleanrr
RUN pip install .

RUN useradd --create-home --shell /bin/bash --uid 1000 cleanrr \
 && mkdir -p /app/data \
 && chown -R cleanrr:cleanrr /app
USER cleanrr

CMD ["python", "-m", "cleanrr"]
