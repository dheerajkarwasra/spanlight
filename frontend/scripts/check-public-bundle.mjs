import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gzipSync } from "node:zlib";

const directory = resolve(process.argv[2] ?? "dist");
const manifest = JSON.parse(readFileSync(resolve(directory, ".vite/manifest.json"), "utf8"));
const roots = [
  "index.html",
  "src/features/landing/landing-page.tsx",
  "src/features/auth/forgot-password-page.tsx",
  "src/features/auth/invite-page.tsx",
  "src/features/auth/reset-password-page.tsx",
  "src/features/auth/verify-email-page.tsx",
];
for (const root of roots) {
  assert(Object.hasOwn(manifest, root), `Missing public route entry: ${root}`);
}
const authRoots = Object.keys(manifest).filter((key) => /^_auth-[^/]+\.js$/.test(key));
assert.equal(authRoots.length, 1, "Expected one shared auth entry for login/signup");
roots.push(...authRoots);
const results = {};
const violations = [];
for (const root of roots) {
  const seen = new Set();
  function visit(key) {
    if (seen.has(key)) return;
    seen.add(key);
    for (const dependency of manifest[key].imports ?? []) visit(dependency);
  }
  visit(root);
  const files = [...new Set([...seen].map((key) => manifest[key].file))].filter((file) =>
    file.endsWith(".js"),
  );
  let raw = 0;
  let gzip = 0;
  for (const file of files) {
    const content = readFileSync(resolve(directory, file));
    raw += content.length;
    gzip += gzipSync(content).length;
    const map = file.includes("/rolldown-runtime-")
      ? { sources: [] }
      : JSON.parse(readFileSync(resolve(directory, `${file}.map`), "utf8"));
    if (
      /charts[-.]/.test(file) ||
      map.sources.some((source) =>
        /node_modules\/(recharts|d3-[^/]+|victory-vendor)\//.test(source),
      )
    ) {
      violations.push(`${root} statically imports chart implementation through ${file}`);
    }
  }
  results[root] = { files, raw, gzip };
}
console.log(JSON.stringify(results, null, 2));

assert.deepEqual(violations, [], "Public routes must not statically import charts");
