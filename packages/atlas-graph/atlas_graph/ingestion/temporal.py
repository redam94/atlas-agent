"""TEMPORAL_NEAR — same-project Documents within a rolling N-day window."""

# Both endpoints must have a non-null created_at; null comparisons in
# duration.between would evaluate to null and silently drop the predicate.
# duration.between(a, b).days is signed, so we check |delta| <= window.
TEMPORAL_NEAR_CYPHER = (
    "MATCH (d_new:Document {id: $document_id}), (d:Document) "
    "WHERE d.project_id = $project_id "
    "  AND d.id <> d_new.id "
    "  AND d.created_at IS NOT NULL "
    "  AND d_new.created_at IS NOT NULL "
    "  AND duration.between(datetime(d.created_at), datetime(d_new.created_at)).days "
    "      <= $window_days "
    "  AND duration.between(datetime(d.created_at), datetime(d_new.created_at)).days "
    "      >= -$window_days "
    "MERGE (d_new)-[:TEMPORAL_NEAR]-(d)"
)
