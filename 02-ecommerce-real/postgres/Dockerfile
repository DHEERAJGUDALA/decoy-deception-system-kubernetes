# Lightweight PostgreSQL with pre-loaded schema and seed data.
# Based on alpine for minimal footprint (~80MB image).
# init.sql runs automatically on first start via the entrypoint directory.
FROM postgres:15-alpine

COPY init.sql /docker-entrypoint-initdb.d/01-init.sql
