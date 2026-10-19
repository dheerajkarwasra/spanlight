import { Search } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import { Input } from "@/components/ui/input";

import { useDebouncedCallback } from "./use-debounced-callback";

const SEARCH_DEBOUNCE_MS = 300;

interface TraceSearchInputProps {
  value: string;
  onCommit: (value: string) => void;
}

/**
 * Free-text search. Typing is local and debounced; the URL is the source of
 * truth, so an external change (e.g. "Clear all") resets the draft.
 */
export function TraceSearchInput({ value, onCommit }: TraceSearchInputProps) {
  const [draft, setDraft] = useState(value);
  const [syncedValue, setSyncedValue] = useState(value);
  const debounced = useDebouncedCallback(onCommit, SEARCH_DEBOUNCE_MS);

  if (value !== syncedValue) {
    setSyncedValue(value);
    setDraft(value);
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
    <div className="relative w-full md:w-80">
      <Search
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-4 size-4 -translate-y-1/2 text-subtle-foreground"
      />
      <Input
        type="search"
        aria-label="Search traces"
        placeholder="Search name, ID, input or output"
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          debounced.schedule(event.target.value);
        }}
        onKeyDown={handleKeyDown}
        className="h-10 rounded-full border-border pr-4 pl-10.5 shadow-card"
      />
    </div>
  );
}
