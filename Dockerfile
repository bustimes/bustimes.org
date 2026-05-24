FROM node:20-slim

WORKDIR /app/

COPY package.json package-lock.json .npmrc /app/
RUN npm ci

COPY frontend /app/frontend
COPY .parcelrc tsconfig.json /app/
RUN npm run lint && npm run build


FROM ghcr.io/jclgoodwin/bustimes.org/bustimes-base:3.14

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app/

COPY uv.lock pyproject.toml /app/
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

COPY --from=0 /app/node_modules/htmx.org/dist /app/node_modules/htmx.org/dist
COPY --from=0 /app/node_modules/reqwest/reqwest.min.js /app/node_modules/reqwest/
COPY --from=0 /app/busstops/static /app/busstops/static
COPY . /app/

ENV PORT=8000 STATIC_ROOT=/staticfiles
RUN ./manage.py collectstatic --noinput

EXPOSE 8000
CMD ["gunicorn", "buses.wsgi"]
