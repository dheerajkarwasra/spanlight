import { describe, expect, it } from "vitest";

import { metricTiles } from "./evidence-metrics";
import { incidentCitation, isIncidentMetric, showsPollingOffNote } from "./incident-citation";

const METRICS = {
  errors: 142,
  incident_id: "inc_1",
  incident_title: "Elevated errors on Claude Sonnet 4.5",
  incident_url: "https://status.claude.com/incidents/abc",
  incident_impact: "major",
};

describe("incidentCitation", () => {
  it("reads the four incident metrics", () => {
    expect(incidentCitation(METRICS)).toEqual({
      title: "Elevated errors on Claude Sonnet 4.5",
      url: "https://status.claude.com/incidents/abc",
      impact: "major",
    });
  });

  it("is null when no incident matched", () => {
    expect(incidentCitation({ errors: 3 })).toBeNull();
    expect(
      incidentCitation({
        incident_id: null,
        incident_title: null,
        incident_url: null,
        incident_impact: null,
      }),
    ).toBeNull();
  });

  it("drops a link that is not a polled status page", () => {
    for (const url of [
      "javascript:alert(1)",
      "http://status.claude.com/incidents/abc",
      "data:text/html,hi",
      "https://evil.example/x",
      "https://status.x@evil.example/",
      "https://status.anthropic.com/incidents/abc",
      "https://notstatus.claude.com/x",
    ]) {
      expect(incidentCitation({ ...METRICS, incident_url: url })?.url).toBeNull();
    }
  });

  it("keeps the title as text and leaves a missing title or impact unknown", () => {
    const citation = incidentCitation({
      incident_id: "inc_2",
      incident_title: "<script>alert(1)</script>",
      incident_impact: "",
    });
    expect(citation).toEqual({ title: "<script>alert(1)</script>", url: null, impact: null });
    expect(incidentCitation({ incident_id: "inc_3" })?.title).toBeNull();
  });
});

describe("isIncidentMetric", () => {
  it("picks out the incident metrics so they are not drawn as tiles", () => {
    expect(isIncidentMetric("incident_title")).toBe(true);
    expect(isIncidentMetric("errors")).toBe(false);
    expect(metricTiles(METRICS).map((tile) => tile.name)).toEqual(["errors"]);
  });
});

describe("showsPollingOffNote", () => {
  it("applies to a provider incident finding without a match while polling is off", () => {
    expect(showsPollingOffNote("provider_incident", { incident_id: null }, "disabled")).toBe(true);
  });

  it("does not apply otherwise", () => {
    expect(showsPollingOffNote("provider_incident", { incident_id: null }, "enabled")).toBe(false);
    expect(showsPollingOffNote("provider_incident", METRICS, "disabled")).toBe(false);
    expect(showsPollingOffNote("error_spike", {}, "disabled")).toBe(false);
    expect(showsPollingOffNote("provider_incident", {}, undefined)).toBe(false);
  });
});
