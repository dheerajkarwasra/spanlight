/**
 * The legal pages name the spanlight.dev operator, so only that build ships them.
 * Set VITE_LEGAL_PAGES=1 at build time (deploy/Dockerfile LEGAL_PAGES); self-hosted builds leave it empty.
 */
export const LEGAL_PAGES_ENABLED = import.meta.env.VITE_LEGAL_PAGES === "1";
