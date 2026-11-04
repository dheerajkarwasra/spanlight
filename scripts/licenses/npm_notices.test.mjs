// Tests for npm_notices.mjs on a tiny installed package tree (node --test, Node stdlib only).
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT = fileURLToPath(new URL("./npm_notices.mjs", import.meta.url));

function writePackage(dir, manifest, files = {}) {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "package.json"), JSON.stringify(manifest));
  for (const [name, text] of Object.entries(files))
    writeFileSync(join(dir, name), text);
}

function fixture(packages) {
  const root = mkdtempSync(join(tmpdir(), "npm-notices-"));
  const dependencies = {};
  const devDependencies = {};
  for (const { manifest, files, dev } of packages) {
    (dev ? devDependencies : dependencies)[manifest.name] = manifest.version;
    writePackage(join(root, "node_modules", manifest.name), manifest, files);
  }
  writePackage(root, {
    name: "fixture-root",
    version: "0.0.0",
    dependencies,
    devDependencies,
  });
  return root;
}

function run(root, ...args) {
  return spawnSync(process.execPath, [SCRIPT, "--dir", root, ...args], {
    encoding: "utf8",
  });
}

test("writes one section per production package", () => {
  const root = fixture([
    {
      manifest: { name: "alpha", version: "1.0.0", license: "MIT" },
      files: { LICENSE: "MIT text of alpha\n" },
    },
    {
      manifest: { name: "beta", version: "2.1.0", license: "ISC" },
      files: { "NOTICE.md": "beta notice\n" },
    },
    // A dev-only package is not shipped, so its missing licence does not matter.
    { manifest: { name: "devonly", version: "9.9.9" }, dev: true },
  ]);
  const out = join(root, "notices.txt");

  const result = run(root, "--output", out);

  assert.equal(result.status, 0, result.stderr);
  const text = readFileSync(out, "utf8");
  assert.match(text, /alpha 1\.0\.0\nLicense: MIT\n/);
  assert.match(text, /MIT text of alpha/);
  assert.match(text, /beta 2\.1\.0\nLicense: ISC\n[\s\S]*beta notice/);
  assert.doesNotMatch(text, /devonly/);
  assert.ok(text.indexOf("alpha 1.0.0") < text.indexOf("beta 2.1.0"));
});

test("sorts by code point, so the order does not depend on the locale", () => {
  const root = fixture([
    { manifest: { name: "alpha", version: "1.0.0", license: "MIT" } },
    { manifest: { name: "Zeta", version: "1.0.0", license: "MIT" } },
  ]);
  const out = join(root, "notices.txt");

  const result = run(root, "--output", out);

  assert.equal(result.status, 0, result.stderr);
  const text = readFileSync(out, "utf8");
  assert.ok(text.indexOf("Zeta 1.0.0") < text.indexOf("alpha 1.0.0"));
});

test("a production package without a licence fails and is named", () => {
  const root = fixture([
    { manifest: { name: "alpha", version: "1.0.0", license: "MIT" } },
    { manifest: { name: "nolicence", version: "0.3.0" } },
  ]);

  const result = run(root, "--check");

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /nolicence 0\.3\.0/);
  assert.doesNotMatch(result.stderr, /alpha/);
});
