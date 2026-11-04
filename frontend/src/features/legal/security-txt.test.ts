import securityTxt from "../../../public/.well-known/security.txt?raw";

// RFC 9116 fields, one "Name: value" per line; comments start with "#".
const fields = new Map<string, string[]>();
for (const line of securityTxt.split(/\r?\n/)) {
  const match = /^([A-Za-z-]+):\s*(.+)$/.exec(line);
  if (!match?.[1] || !match[2]) continue;
  const name = match[1].toLowerCase();
  fields.set(name, [...(fields.get(name) ?? []), match[2].trim()]);
}

describe("/.well-known/security.txt", () => {
  it("names a contact and the policy", () => {
    expect(fields.get("contact")?.[0]).toMatch(/^https:\/\//);
    expect(fields.get("policy")?.[0]).toMatch(/^https:\/\/.+SECURITY\.md$/);
    expect(fields.get("preferred-languages")).toEqual(["en"]);
  });

  it("expires once, in RFC 3339, at least 60 days from now", () => {
    const expires = fields.get("expires") ?? [];
    expect(expires).toHaveLength(1);
    expect(expires[0]).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/);
    const daysLeft = (Date.parse(expires[0] ?? "") - Date.now()) / 86_400_000;
    // Fails CI two months before the file lapses, so there is time to renew it.
    expect(daysLeft).toBeGreaterThanOrEqual(60);
  });
});
