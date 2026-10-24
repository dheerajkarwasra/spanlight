import { getRouteApi } from "@tanstack/react-router";
import { Search } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import { Input } from "@/components/ui/input";
import { useDebouncedCallback } from "@/lib/use-debounced-callback";

const sessionsRoute = getRouteApi("/_authed/$orgId/$projectId/sessions");

const SEARCH_DEBOUNCE_MS = 250;

/**
 * "Session ID or user ID" search (spec §8). Typing is local and reaches the URL's `q` after 250 ms of quiet
 * (Enter sends it now, Escape clears it); the URL is the source of truth, so a change from
 * elsewhere (Back, a link) resets the draft.
 */
export function SessionSearchInput() {
  const { q = "" } = sessionsRoute.useSearch();
  const navigate = sessionsRoute.useNavigate();
  const [draft, setDraft] = useState(q);
  const [synced, setSynced] = useState(q);
  const debounced = useDebouncedCallback((value: string) => {
    const next = value.trim();
    if (next === q) return;
    // Replace, so Back skips the partial searches typed on the way.
    void navigate({ search: (previous) => ({ ...previous, q: next || undefined }), replace: true });
  }, SEARCH_DEBOUNCE_MS);

  if (q !== synced) {
    setSynced(q);
    setDraft(q);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      debounced.flush();
    }
    if (event.key === "Escape" && draft !== "") {
      event.preventDefault();
      setDraft("");
      debounced.schedule("");
      debounced.flush();
    }
  }

  return (
    <div className="relative w-full md:w-72">
      <Search
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-3.5 size-3.5 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        type="search"
        aria-label="Search by session ID or user ID"
        placeholder="Session ID or user ID"
        maxLength={256}
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          debounced.schedule(event.target.value);
        }}
        onKeyDown={handleKeyDown}
        className="h-10 rounded-full pl-9"
      />
    </div>
  );
}
