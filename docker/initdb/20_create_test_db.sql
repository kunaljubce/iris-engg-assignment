-- Runs once, on first start with an empty data volume (after the image's 10_postgis.sh).
-- Creates the separate database used by the db-marked tests, so tests never touch `iris`.
-- The postgres entrypoint runs this as POSTGRES_USER, which owns the new database.

CREATE DATABASE iris_test;

\connect iris_test

-- 10_postgis.sh only installs PostGIS into POSTGRES_DB, so install it here explicitly.
CREATE EXTENSION IF NOT EXISTS postgis;
