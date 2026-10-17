# Status feed fixtures

Recorded real responses from the provider status feeds that `app/provider_status/adapters`
parses. They were fetched on 2026-10-16 (the file names carry the fetch date) with:

```bash
curl -sS -H 'accept-encoding: identity' https://status.claude.com/api/v2/incidents.json
curl -sS -H 'accept-encoding: identity' https://status.openai.com/api/v2/incidents.json
curl -sS -H 'accept-encoding: identity' https://status.openai.com/history.rss
curl -sS -H 'accept-encoding: identity' https://status.cloud.google.com/incidents.json
```

| File | Live response | Kept | Notes |
|---|---|---|---|
| `anthropic-incidents-2026-10-16.json` | 50 incidents, 192,600 bytes | the first 10 incidents in feed order (newest first) | re-serialised with two-space indentation |
| `openai-incidents-2026-10-16.json` | 25 incidents, 33,014 bytes | the first 10 incidents in feed order (newest first) | re-serialised with two-space indentation |
| `openai-history-2026-10-16.rss` | 108 items, 105,585 bytes | the first 10 items (newest first) | the kept bytes are unchanged; only the later items were cut |
| `google-incidents-2026-10-16.json` | 7 incidents, 192,452 bytes | all 7 incidents | byte-for-byte as served; one of them (`41E5S3mkTGDfkZuJZH5k`) names a Vertex AI or Gemini product |

Expected parse counts, which `test_adapters.py` asserts:

- Anthropic: 10 incidents.
- OpenAI JSON: 10 incidents.
- OpenAI RSS fallback: 10 incidents.
- Google: 1 incident (the other 6 affect no Vertex AI or Gemini product and are filtered out).

Hostile fixtures, written by hand:

- `billion-laughs.rss`: an RSS document with nested entity definitions (entity expansion).
  The parser must refuse it with `FeedParseError`.
- `script-title.json`: a Statuspage-shaped feed whose single incident has the title
  `<script>alert(1)</script>`. The title must be stored as text with the tags removed.
