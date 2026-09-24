-- ============================================================================
-- 06 - Functions
-- ============================================================================

-- ---------------------------------------------------------------------------
-- RAG similarity search over the knowledge base (used by the conversational AI)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION search_knowledge_base(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 5
)
RETURNS TABLE (
    document_id int,
    title varchar,
    content text,
    similarity float
)
LANGUAGE sql STABLE
AS $$
    SELECT
        document_id,
        title,
        content,
        1 - (embedding <=> query_embedding) AS similarity
    FROM knowledge_base
    WHERE 1 - (embedding <=> query_embedding) > match_threshold
      AND is_active = TRUE
    ORDER BY embedding <=> query_embedding
    LIMIT match_count;
$$;

-- ---------------------------------------------------------------------------
-- Refresh the rollup materialized views.
-- Call on a schedule (e.g. hourly) from the API/analytics layer or a CronJob.
-- Uses CONCURRENTLY (non-locking) once a view is populated; falls back to a
-- plain REFRESH the first time, because CONCURRENTLY cannot run against a
-- materialized view that was created WITH NO DATA and never populated.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION refresh_rollups()
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    mv     text;
    is_pop boolean;
BEGIN
    FOREACH mv IN ARRAY ARRAY[
        'sensor_readings_hourly',
        'daily_prediction_summary',
        'wind_data_hourly'
    ]
    LOOP
        SELECT ispopulated INTO is_pop FROM pg_matviews WHERE matviewname = mv;
        IF is_pop THEN
            EXECUTE format('REFRESH MATERIALIZED VIEW CONCURRENTLY %I', mv);
        ELSE
            EXECUTE format('REFRESH MATERIALIZED VIEW %I', mv);
        END IF;
    END LOOP;
END;
$$;
