-- Recompute historical prompt-cache hit rate metrics from persisted token costs.
-- Safe to rerun: only updates existing conversation_metrics rows named cache_hit_rate.

UPDATE conversation_metrics
SET metric_value = (
    SELECT
        CASE
            WHEN conversation_costs.token_input > 0
            THEN
                CASE
                    WHEN conversation_costs.cached_tokens > conversation_costs.token_input
                    THEN 1.0
                    ELSE conversation_costs.cached_tokens * 1.0 / conversation_costs.token_input
                END
            ELSE 0.0
        END
    FROM conversation_costs
    WHERE conversation_costs.run_id = conversation_metrics.run_id
)
WHERE metric_name = 'cache_hit_rate'
  AND EXISTS (
      SELECT 1
      FROM conversation_costs
      WHERE conversation_costs.run_id = conversation_metrics.run_id
  );
