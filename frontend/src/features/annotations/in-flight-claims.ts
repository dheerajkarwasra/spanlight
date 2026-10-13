import { annotationsApi, type QueueItem } from "@/lib/api";

/** The queue a work session is on: the project and the queue within it. */
export interface SessionKey {
  projectId: string;
  queueId: string;
}

/** A claim request on the wire, shared by every session on its queue. */
export interface InFlightClaim {
  answer: Promise<QueueItem | undefined>;
  /** Set when the session that sent it goes away; cleared when a new session adopts it. */
  orphaned: boolean;
  /** Set once an orphan's item has been given back, so a second abandoned session does not. */
  released: boolean;
}

/**
 * Claims still waiting for their answer, by queue. Module-level because the page can remount as a
 * new component on the same queue while its first claim is on the wire (the app shell swaps its
 * layout when navigation into work mode resolves; StrictMode does the same in development). A
 * second claim sent then would skip the row the first one still holds and come back empty, ending
 * the session while an item is pending, so the remounted page adopts the first claim instead.
 */
const inFlightClaims = new Map<string, InFlightClaim>();

/**
 * The claim a session on `key` should wait on: an orphaned one still on the wire for that queue,
 * adopted, or else a new claim request. A claim is forgotten once it answers or fails.
 */
export function claimFor(key: SessionKey): InFlightClaim {
  const id = `${key.projectId}/${key.queueId}`;
  const pending = inFlightClaims.get(id);
  if (pending?.orphaned) {
    pending.orphaned = false;
    return pending;
  }
  const claim: InFlightClaim = {
    answer: annotationsApi.claim(key.projectId, key.queueId),
    orphaned: false,
    released: false,
  };
  inFlightClaims.set(id, claim);
  const forget = () => {
    if (inFlightClaims.get(id) === claim) {
      inFlightClaims.delete(id);
    }
  };
  claim.answer.then(forget, forget);
  return claim;
}
