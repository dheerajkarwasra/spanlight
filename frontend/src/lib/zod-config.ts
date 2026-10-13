import { z } from "zod";

/*
 * Zod probes for `new Function` support the first time it validates, and our Content-Security-Policy
 * (no 'unsafe-eval') reports that probe as a violation on every page load. Jitless mode skips the
 * probe; validation results are identical. zod probes when a schema is built, often at import
 * time, so this must run before any module that builds one: main.tsx imports it first, and the
 * production build puts it in zod's own chunk (vite.config.ts), which every schema chunk loads
 * first.
 */
z.config({ jitless: true });
