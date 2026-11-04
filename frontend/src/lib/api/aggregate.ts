/** Source coverage for the last materialized aggregate snapshot. */
export interface AggregateMetadata {
  aggregate_status?: "complete" | "pending" | "unavailable";
  oldest_pending_at?: string | null;
}

/** List headers live beside the items so query caching preserves metadata even for empty lists. */
export interface AggregateList<T> extends AggregateMetadata {
  items: T[];
}
