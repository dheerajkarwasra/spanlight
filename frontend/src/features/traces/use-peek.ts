import { getRouteApi, useRouter } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import { useMediaQuery } from "@/lib/use-media-query";

import { stepPeek } from "./peek-nav";

const tracesRoute = getRouteApi("/_authed/$orgId/$projectId/traces");

/** From this width a row click peeks at the trace in a sheet; below, it opens the trace page. */
const PEEK_QUERY = "(min-width: 1280px)";

export type Peek = ReturnType<typeof usePeek>;

/** A row's name link (`TraceName`), to scroll the peeked row into view and return focus to it. */
export function traceRowLink(traceId: string): HTMLElement | undefined {
  return [...document.querySelectorAll<HTMLElement>("a[data-trace-id]")].find(
    (link) => link.dataset.traceId === traceId,
  );
}

/**
 * The peeked trace, held in the list's URL as `peek=` (spec ruling 10). Opening pushes a history
 * entry and j/k replace it, so Back returns to the list; closing goes back when the peek was
 * opened here, else (a shared link) drops `peek` in place. `ids` are the loaded rows j/k step;
 * `enabled` is false below 1280 px, where rows open the trace page and no sheet shows.
 */
export function usePeek(ids: readonly string[]) {
  const peekId = tracesRoute.useSearch({ select: (search) => search.peek });
  const navigate = tracesRoute.useNavigate();
  const router = useRouter();
  const openedHere = useRef(false);
  // Closed any way (Back, a link): a later shared-link peek must not go back past the list.
  useEffect(() => {
    if (peekId === undefined) openedHere.current = false;
  }, [peekId]);

  function show(peek: string | undefined, replace: boolean) {
    void navigate({ search: (prev) => ({ ...prev, peek }), replace, resetScroll: false });
  }

  return {
    enabled: useMediaQuery(PEEK_QUERY),
    peekId,
    ids,
    open: (id: string) => {
      openedHere.current = true;
      show(id, false);
    },
    close: () => {
      if (openedHere.current) {
        openedHere.current = false;
        router.history.back();
      } else {
        show(undefined, true);
      }
    },
    step: (delta: 1 | -1) => {
      const next = peekId === undefined ? null : stepPeek(ids, peekId, delta);
      if (next !== null) {
        show(next, true);
      }
    },
  };
}
