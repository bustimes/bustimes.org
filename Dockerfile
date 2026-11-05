FROM node:24-slim

WORKDIR /app/

COPY package.json package-lock.json .npmrc /app/
RUN npm ci

COPY frontend /app/frontend
COPY .parcelrc tsconfig.json /app/
RUN npm run lint && npm run build


FROM ghcr.io/bustimes/bustimes.org/bustimes-base:3.15 AS venv

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

WORKDIR /app/

COPY uv.lock pyproject.toml /app/
RUN uv sync --frozen --no-dev --compile-bytecode


FROM python:3.15-slim-trixie

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends libgdal36 libpq5 && \
    rm -rf /var/lib/apt /var/lib/dpkg/info/*

WORKDIR /app/

COPY --from=venv /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY --from=0 /app/node_modules/htmx.org/dist /app/node_modules/htmx.org/dist
COPY --from=0 /app/node_modules/reqwest/reqwest.min.js /app/node_modules/reqwest/
COPY --from=0 /app/busstops/static /app/busstops/static
COPY . /app/

ENV PORT=8000 STATIC_ROOT=/staticfiles
RUN ./manage.py check --tag urls && ./manage.py collectstatic --noinput
RUN python -m compileall -q -j 0 -x "/(\.venv|node_modules)/" .

EXPOSE 8000 9090
CMD ["granian", "--host", "0.0.0.0", "--interface", "wsgi", "--respawn-failed-workers", "--metrics", "--metrics-address", "0.0.0.0", "buses.wsgi:application"]
