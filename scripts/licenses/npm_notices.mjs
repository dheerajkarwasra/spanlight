// Write the third-party licence notices for an npm project's production dependencies.
//
//   node scripts/licenses/npm_notices.mjs --dir frontend --output frontend/dist/third-party-notices.txt
//   node scripts/licenses/npm_notices.mjs --dir frontend --check
//
// The tree comes from `npm ls --omit=dev --all --json --long` in the installed project. Each
// section holds the name, version, the package.json licence and the LICENSE / COPYING / NOTICE
// files the package ships. A package without a licence fails the run (exit 1), naming it.
// Node standard library only.
import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { parseArgs } from "node:util";

const RULE = "=".repeat(80);
const LICENCE_FILE = /^(licen[cs]e|copying|notice)/i;

function licenceId(manifest) {
  const { license, licenses } = manifest;
  if (typeof license === "string" && license.trim()) return license.trim();
  if (license?.type) return String(license.type);
  // Legacy form: "licenses": [{ "type": "MIT" }, ...]
  const legacy = Array.isArray(licenses)
    ? licenses.map((l) => l?.type ?? l).filter(Boolean)
    : [];
  return legacy.length ? legacy.join(" OR ") : null;
}

function licenceTexts(dir) {
  return readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && LICENCE_FILE.test(entry.name))
    .map((entry) => entry.name)
    .sort()
    .map((name) => readFileSync(join(dir, name), "utf8").trim());
}

function collect(dependencies, found) {
  for (const [name, dep] of Object.entries(dependencies ?? {})) {
    // An optional dependency for another platform, or an unmet peer, is listed without a path.
    if (dep.path && dep.version && !dep.extraneous)
      found.set(`${name}@${dep.version}`, { name, ...dep });
    collect(dep.dependencies, found);
  }
  return found;
}

const { values: args } = parseArgs({
  options: {
    dir: { type: "string", default: "." },
    output: { type: "string" },
    check: { type: "boolean" },
  },
});
if (Boolean(args.output) === Boolean(args.check)) {
  console.error("Pass exactly one of --output FILE or --check.");
  process.exit(2);
}

// npm ls exits non-zero for problems such as a missing optional dependency; the JSON still
// describes the installed tree, so only unreadable output is an error.
const ls = spawnSync("npm", ["ls", "--omit=dev", "--all", "--json", "--long"], {
  cwd: args.dir,
  encoding: "utf8",
  maxBuffer: 256 * 1024 * 1024,
});
let tree;
try {
  tree = JSON.parse(ls.stdout);
} catch {
  console.error(`npm ls gave no readable tree in ${args.dir}:\n${ls.stderr}`);
  process.exit(2);
}

// Code-point order (not localeCompare), so the file is identical in CI and in the image.
const byCodePoint = (x, y) => (x < y ? -1 : x > y ? 1 : 0);
const packages = [...collect(tree.dependencies, new Map()).values()].sort(
  (a, b) => byCodePoint(a.name, b.name) || byCodePoint(a.version, b.version),
);
const sections = [];
const missing = [];
for (const pkg of packages) {
  const id = licenceId(
    JSON.parse(readFileSync(join(pkg.path, "package.json"), "utf8")),
  );
  if (!id) {
    missing.push(`${pkg.name} ${pkg.version}`);
    continue;
  }
  const texts = licenceTexts(pkg.path);
  const body = texts.length
    ? texts.join("\n\n")
    : "(The package ships no licence file.)";
  sections.push(
    `${RULE}\n${pkg.name} ${pkg.version}\nLicense: ${id}\n${"-".repeat(80)}\n${body}`,
  );
}

if (missing.length) {
  console.error(
    `Packages without a licence identifier:\n  ${missing.join("\n  ")}`,
  );
  process.exit(1);
}
if (args.output) {
  // Both npm trees ship in the web image; this names what the npm tree cannot list.
  const header = [
    `Third-party npm packages shipped with ${tree.name ?? "this project"}, with their licences.`,
    "",
    "Not listed below (they are not npm packages):",
    "- The Caddy web server, and the Go modules and Go standard library compiled into it:",
    "  /third-party-notices-caddy.txt (in the web image: /srv/third-party-notices-caddy.txt).",
    "- The Alpine Linux packages of the web image: the apk database /lib/apk/db/installed",
    "  records each package's licence (its L: lines).",
    "",
  ].join("\n");
  writeFileSync(args.output, `${header}\n${sections.join("\n\n")}\n`);
}
console.error(`${sections.length} packages, all with a licence identifier.`);
