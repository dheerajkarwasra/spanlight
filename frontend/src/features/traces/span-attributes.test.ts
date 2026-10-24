import { describe, expect, it } from "vitest";

import { splitTransportAttributes } from "./span-attributes";

describe("splitTransportAttributes", () => {
  it("moves the transport keys aside and keeps both lists in their original order", () => {
    expect(
      splitTransportAttributes({
        "http.method": "POST",
        "app.feature": "triage",
        "net.peer.name": "api.openai.com",
        "url.full": "https://api.openai.com/v1/chat/completions",
        "server.address": "api.openai.com",
        "network.protocol.version": "1.1",
        "user_agent.original": "spanlight-python/1.0",
        "gen_ai.system": "openai",
      }),
    ).toEqual({
      main: [
        ["app.feature", "triage"],
        ["gen_ai.system", "openai"],
      ],
      transport: [
        ["http.method", "POST"],
        ["net.peer.name", "api.openai.com"],
        ["url.full", "https://api.openai.com/v1/chat/completions"],
        ["server.address", "api.openai.com"],
        ["network.protocol.version", "1.1"],
        ["user_agent.original", "spanlight-python/1.0"],
      ],
    });
  });

  it("matches the dotted prefix only", () => {
    expect(splitTransportAttributes({ http: 1, httpx: 2, network_id: 3 }).transport).toEqual([]);
  });

  it("handles no attributes", () => {
    expect(splitTransportAttributes({})).toEqual({ main: [], transport: [] });
  });
});
