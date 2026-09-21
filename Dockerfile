FROM node:22-alpine AS frontend
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY iros_catalog ./iros_catalog
RUN pip install --no-cache-dir .
COPY data/iros.sqlite ./data/iros.sqlite
RUN python -m iros_catalog --db data/iros.sqlite atlas-build
COPY --from=frontend /web/dist ./web/dist
EXPOSE 8080
CMD ["uvicorn", "iros_catalog.api:app", "--host", "0.0.0.0", "--port", "8080"]
