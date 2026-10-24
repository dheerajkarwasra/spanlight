import type { Bucket } from "@/lib/api";

import { BUCKET_MS } from "./chart-data";

/** The traces list's search for one chart bucket. */
export interface BucketSearch {
  range: "custom";
  from: string;
  to: string;
  env?: string;
  status?: "error";
}

/**
 * The traces of one Calls & errors bucket (spec O4): a custom range over the bucket, in the
 * page's environment, and only the failed traces when the failed segment was clicked.
 */
export function bucketSearch(
  bucketStart: string,
  bucket: Bucket,
  env?: string,
  errorsOnly = false,
): BucketSearch {
  const start = Date.parse(bucketStart);
  const search: BucketSearch = {
    range: "custom",
    from: new Date(start).toISOString(),
    to: new Date(start + BUCKET_MS[bucket]).toISOString(),
  };
  if (env) {
    search.env = env;
  }
  if (errorsOnly) {
    search.status = "error";
  }
  return search;
}
