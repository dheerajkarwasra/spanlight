import { describe, expect, it } from "vitest";

import { ApiError, NetworkError } from "@/lib/api";

import {
  countsLine,
  feedbackErrorMessage,
  feedbackLabel,
  feedbackStatusParts,
  nextVerdict,
  noteCounter,
} from "./feedback-state";

const NOW = new Date("2026-10-16T12:00:00Z");

describe("nextVerdict", () => {
  it("votes for the clicked verdict when there is none yet", () => {
    expect(nextVerdict(null, "right")).toBe("right");
    expect(nextVerdict(null, "wrong")).toBe("wrong");
  });

  it("switches to the other verdict", () => {
    expect(nextVerdict("right", "wrong")).toBe("wrong");
    expect(nextVerdict("wrong", "right")).toBe("right");
  });

  it("removes the vote when the current verdict is clicked again", () => {
    expect(nextVerdict("right", "right")).toBeNull();
    expect(nextVerdict("wrong", "wrong")).toBeNull();
  });
});

describe("feedbackLabel", () => {
  it("says the verdict and the day it was given", () => {
    expect(feedbackLabel({ verdict: "right", updatedAt: "2026-10-14T12:00:00Z" }, NOW)).toBe(
      "You said this was right on 14 Oct",
    );
    expect(feedbackLabel({ verdict: "wrong", updatedAt: "2026-10-02T12:00:00Z" }, NOW)).toBe(
      "You said this was wrong on 2 Oct",
    );
  });

  it("adds the year for a vote from another year", () => {
    expect(feedbackLabel({ verdict: "right", updatedAt: "2025-12-30T12:00:00Z" }, NOW)).toBe(
      "You said this was right on 30 Dec 2025",
    );
  });

  it("leaves the day out when it is not known", () => {
    expect(feedbackLabel({ verdict: "right", updatedAt: null }, NOW)).toBe(
      "You said this was right",
    );
    expect(feedbackLabel({ verdict: "wrong", updatedAt: "not a date" }, NOW)).toBe(
      "You said this was wrong",
    );
  });
});

describe("countsLine", () => {
  it("counts every member's votes", () => {
    expect(countsLine(2, 0)).toBe("2 said right · 0 said wrong");
    expect(countsLine(1234, 1)).toBe("1,234 said right · 1 said wrong");
  });
});

describe("feedbackStatusParts", () => {
  const base = { right: 2, wrong: 0, savedAt: null, noteOpen: false, canVote: true };

  it("explains the question before a member votes", () => {
    expect(feedbackStatusParts({ ...base, mine: null }, NOW)).toEqual({
      own: "Your answer measures how often this check is right.",
      counts: "2 said right · 0 said wrong.",
    });
  });

  it("shows only the counts to a viewer", () => {
    expect(feedbackStatusParts({ ...base, mine: null, canVote: false }, NOW)).toEqual({
      own: null,
      counts: "2 said right · 0 said wrong.",
    });
  });

  it("confirms the saved vote while the note is open", () => {
    expect(feedbackStatusParts({ ...base, wrong: 1, mine: "wrong", noteOpen: true }, NOW)).toEqual({
      own: "Saved: you said this was wrong.",
      counts: "2 said right · 1 said wrong.",
    });
  });

  it("names the verdict and its day once the note is closed", () => {
    expect(
      feedbackStatusParts({ ...base, mine: "right", savedAt: "2026-10-14T12:00:00Z" }, NOW),
    ).toEqual({ own: "You said this was right on 14 Oct", counts: null });
  });
});

describe("noteCounter", () => {
  it("counts characters against the 500 limit", () => {
    expect(noteCounter("")).toBe("0 / 500");
    expect(noteCounter("The errors came from our own proxy, not the provider.")).toBe("53 / 500");
  });
});

describe("feedbackErrorMessage", () => {
  it("names the server's status", () => {
    const error = new ApiError(503, { title: "Service unavailable", status: 503 });
    expect(feedbackErrorMessage("save", error)).toBe(
      "Your answer wasn’t saved: the server answered 503. Try again.",
    );
    expect(feedbackErrorMessage("remove", error)).toBe(
      "Your answer wasn’t removed: the server answered 503. Try again.",
    );
  });

  it("says the server was not reached on a network failure", () => {
    expect(feedbackErrorMessage("save", new NetworkError(new TypeError("fetch failed")))).toBe(
      "Your answer wasn’t saved: the server could not be reached. Try again.",
    );
  });

  it("falls back to a plain sentence for anything else", () => {
    expect(feedbackErrorMessage("save", new Error("boom"))).toBe(
      "Your answer wasn’t saved. Try again.",
    );
  });
});
