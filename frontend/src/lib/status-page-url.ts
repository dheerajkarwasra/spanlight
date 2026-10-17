/**
 * The one gate for links to a provider status page (Provider health's incident list and the
 * Doctor's incident citation). Incident URLs come from the status feeds the server polls, and the
 * poller already keeps only its feeds' own pages; this is the second check, at the point a link
 * is drawn. Only an https URL on one of the polled status hosts, with no user name or password,
 * becomes a link; anything else (another host, `https://status.x@evil.example/`, `javascript:`)
 * returns null and the title stays plain text.
 */
const STATUS_HOSTS: ReadonlySet<string> = new Set([
  "status.claude.com",
  "status.openai.com",
  "status.cloud.google.com",
]);

export function statusPageUrl(value: string | null): string | null {
  if (value === null) {
    return null;
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  const safe =
    url.protocol === "https:" &&
    url.username === "" &&
    url.password === "" &&
    url.port === "" &&
    STATUS_HOSTS.has(url.hostname);
  return safe ? url.href : null;
}
