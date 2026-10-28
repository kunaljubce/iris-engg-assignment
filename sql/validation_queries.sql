-- Post-ingest checks for iris_staging, run after `harvester run`:
--   docker compose exec -T db psql -U iris -d iris -f - < sql/validation_queries.sql
-- Checks marked "expect 0 rows" are violations when they return anything. Duplicate keys
-- and a point without source_coordinates need no check: the uq_source_record and
-- ck_geometry_matches_status constraints make them impossible.

\pset footer off

\echo '1. Latest runs: status and counts per source'
SELECT DISTINCT ON (source)
       source, coalesce(city, 'all') AS city, status, started_at, records_scraped,
       records_filtered_out, records_rejected, records_inserted, records_updated,
       jsonb_array_length(errors) AS errors, error_message
FROM iris_staging.ingest_run
ORDER BY source, started_at DESC;

\echo '2. Run errors by kind, latest run per source'
SELECT r.source, e->>'kind' AS kind, count(*) AS errors, min(e->>'message') AS example
FROM (
    SELECT DISTINCT ON (source) * FROM iris_staging.ingest_run ORDER BY source, started_at DESC
) r, jsonb_array_elements(r.errors) AS e
GROUP BY 1, 2
ORDER BY 1, 3 DESC;

\echo '3. Stored records by source, city and geocode status'
SELECT source, city, geocode_status, count(*) AS records,
       count(geometry) AS with_point, count(publication_date) AS with_date
FROM iris_staging.signal_ingest
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

\echo '4. Quality flags'
SELECT source, city, flag, count(*) AS records
FROM iris_staging.signal_ingest, unnest(quality_flags) AS flag
GROUP BY 1, 2, 3
ORDER BY 1, 2, 4 DESC;

\echo '5. Records without a point, and why'
SELECT source, source_record_id, city, geocode_status, geocode_reason, left(title, 60) AS title
FROM iris_staging.signal_ingest
WHERE geometry IS NULL
ORDER BY 1, 2;

\echo '6. Points outside the city bounding box of the run that wrote them (expect 0 rows)'
SELECT s.source, s.source_record_id, s.city,
       ST_X(ST_Transform(s.geometry, 4326)) AS lon, ST_Y(ST_Transform(s.geometry, 4326)) AS lat
FROM iris_staging.signal_ingest s
JOIN iris_staging.ingest_run r ON r.id = s.ingest_run_id
CROSS JOIN LATERAL (SELECT r.config_snapshot->'bounding_boxes'->s.city AS box) b
WHERE s.geometry IS NOT NULL
  AND NOT ST_Within(
      ST_Transform(s.geometry, 4326),
      ST_MakeEnvelope((box->>'min_lon')::float8, (box->>'min_lat')::float8,
                      (box->>'max_lon')::float8, (box->>'max_lat')::float8, 4326)
  );

\echo '7. Points in EPSG:25832 and back in WGS84'
SELECT source_record_id, city, ST_SRID(geometry) AS srid,
       round(ST_X(geometry)) AS x_25832, round(ST_Y(geometry)) AS y_25832,
       round(ST_X(ST_Transform(geometry, 4326))::numeric, 5) AS lon,
       round(ST_Y(ST_Transform(geometry, 4326))::numeric, 5) AS lat
FROM iris_staging.signal_ingest
WHERE geometry IS NOT NULL
ORDER BY city, source_record_id;

\echo '8. Traceability: each record with its run, source URL and filter evidence'
SELECT s.source, s.source_record_id, r.started_at AS run_started, r.status AS run_status,
       s.matched_keywords, s.raw_data->'filter_evidence' AS evidence, s.detail_url
FROM iris_staging.signal_ingest s
JOIN iris_staging.ingest_run r ON r.id = s.ingest_run_id
ORDER BY 1, 2;

\echo '9. Stale records: not written by the latest completed run covering their city'
-- A run's city is NULL when it covered all of the source's cities; without that condition
-- every Leipzig row would look stale after `harvester run --city dresden`
SELECT s.source, s.source_record_id, s.city, s.updated_at, left(s.title, 60) AS title
FROM iris_staging.signal_ingest s
WHERE s.ingest_run_id <> (
    SELECT r.id FROM iris_staging.ingest_run r
    WHERE r.source = s.source AND r.status = 'completed'
      AND (r.city IS NULL OR r.city = s.city)
    ORDER BY r.started_at DESC LIMIT 1
);
