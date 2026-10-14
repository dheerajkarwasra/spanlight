// The page contract: what a hand-written docs page must contain for its kind.

const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---/;

function frontmatterFields(markdown) {
  const block = FRONTMATTER.exec(markdown);
  const fields = new Set();
  if (!block) return fields;
  for (const line of block[1].split(/\r?\n/)) {
    const match = /^([A-Za-z][\w-]*):\s*(\S.*)$/.exec(line);
    if (match) fields.add(match[1]);
  }
  return fields;
}

const MIN_CARDS = 2;
const MAX_CARDS = 4;

/** The number of cards in the first <NextSteps> element of the text: its `href:` entries. */
function nextStepCards(text) {
  const start = text.search(/<NextSteps[\s>/]/);
  const end = text.indexOf('/>', start);
  const element = end === -1 ? text.slice(start) : text.slice(start, end + 2);
  return (element.match(/\bhref:/g) ?? []).length;
}

const hasHeading = (markdown, heading) =>
  new RegExp(`^## ${heading.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[ \\t]*$`, 'm').test(markdown);

function checkPrerequisites(markdown, problems) {
  if (!/<Prerequisites[\s>/]/.test(markdown)) problems.push('missing <Prerequisites>');
}

function checkNextSteps(markdown, problems) {
  const next = /^## Next steps[ \t]*$/m.exec(markdown);
  if (!next) problems.push('missing ## Next steps');
  else if (!/<NextSteps[\s>/]/.test(markdown.slice(next.index))) {
    problems.push('missing <NextSteps> after ## Next steps');
  } else {
    const count = nextStepCards(markdown.slice(next.index));
    if (count < MIN_CARDS || count > MAX_CARDS) {
      problems.push(`<NextSteps> has ${count} cards, expected ${MIN_CARDS} to ${MAX_CARDS}`);
    }
  }
}

function requireHeadings(markdown, headings, problems) {
  for (const heading of headings) if (!hasHeading(markdown, heading)) problems.push(`missing ## ${heading}`);
}

// A version a reader can reproduce: dotted numbers, with an optional pre-release or build suffix
// (2.3.0, 0.45, 2.3.0rc1, 2.3.0-beta.1). Ranges, wildcards and tags (^2.3.0, 2.x, latest) are not.
const EXACT_VERSION = /^\d+(?:\.\d+)+(?:[-+.]?(?:a|b|rc|alpha|beta|post|dev|pre)[0-9A-Za-z.-]*|[-+][0-9A-Za-z.-]+)?$/;

function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

/**
 * The first <Verified .../> element of a page: its date and its packages. `note` is ignored.
 * Null when the page has none.
 * @param {string} markdown
 * @returns {{ date: string, packages: { name: string, version: string }[] } | null}
 */
export function verifiedBlock(markdown) {
  const start = markdown.search(/<Verified[\s>/]/);
  if (start === -1) return null;
  const end = markdown.indexOf('/>', start);
  // The note is free text, so it is cut out before the other attributes are read.
  const element = (end === -1 ? markdown.slice(start) : markdown.slice(start, end + 2)).replace(
    /\bnote=(["'])[\s\S]*?\1/,
    '',
  );
  const date = /\bdate=(["'])(.*?)\1/.exec(element)?.[2] ?? '';
  const packages = [...element.matchAll(/name:\s*(["'])(.+?)\1\s*,\s*version:\s*(["'])(.+?)\3/g)].map((match) => ({
    name: match[2],
    version: match[4],
  }));
  return { date, packages };
}

function checkVerified(markdown, problems, { needsPackages }) {
  const block = verifiedBlock(markdown);
  if (!block) {
    problems.push('missing <Verified>');
    return;
  }
  if (!validDate(block.date)) problems.push('Verified date is not YYYY-MM-DD');
  if (needsPackages && block.packages.length === 0) problems.push('<Verified> lists no packages');
  for (const { name, version } of block.packages) {
    if (!EXACT_VERSION.test(version)) problems.push(`Verified version for ${name} is not exact`);
  }
}

/**
 * Returns the contract violations of a page, empty when the page is fine.
 * @param {string} markdown
 * @param {'how-to' | 'concept' | 'reference' | 'integration' | 'tutorial' | 'migration'} kind
 * @returns {string[]}
 */
export function checkPage(markdown, kind) {
  const problems = [];
  const fields = frontmatterFields(markdown);
  if (!fields.has('title')) problems.push('missing frontmatter title');
  if (!fields.has('description')) problems.push('missing frontmatter description');

  if (kind === 'how-to') {
    checkPrerequisites(markdown, problems);
    requireHeadings(markdown, ['Check it worked', 'Troubleshooting'], problems);
    checkNextSteps(markdown, problems);
  } else if (kind === 'concept' || kind === 'reference') {
    if (!hasHeading(markdown, 'Related pages')) problems.push('missing ## Related pages');
  } else if (kind === 'integration') {
    checkPrerequisites(markdown, problems);
    checkVerified(markdown, problems, { needsPackages: true });
    requireHeadings(markdown, ['What is recorded', 'What is not recorded', 'Troubleshooting'], problems);
    checkNextSteps(markdown, problems);
  } else if (kind === 'tutorial') {
    checkPrerequisites(markdown, problems);
    checkVerified(markdown, problems, { needsPackages: true });
    if (!/<Steps[\s>]/.test(markdown)) problems.push('missing <Steps>');
    requireHeadings(markdown, ['Check it worked', 'Troubleshooting'], problems);
    checkNextSteps(markdown, problems);
  } else if (kind === 'migration') {
    checkPrerequisites(markdown, problems);
    checkVerified(markdown, problems, { needsPackages: false });
    requireHeadings(
      markdown,
      ['Concepts side by side', 'Change your code', 'Move your data', 'What has no equivalent'],
      problems,
    );
    checkNextSteps(markdown, problems);
    if (!citedSources(markdown).some((source) => source.startsWith('https://'))) {
      problems.push('cites no https:// source');
    }
  }
  return problems;
}

/**
 * The repository paths listed in a `Sources:` comment, in either the MDX
 * ({/* Sources: a, b *\/}) or the HTML (<!-- Sources: a, b -->) form.
 * @param {string} markdown
 * @returns {string[]}
 */
export function citedSources(markdown) {
  const match = /\{\/\*\s*Sources:([\s\S]*?)\*\/\}|<!--\s*Sources:([\s\S]*?)-->/.exec(markdown);
  if (!match) return [];
  return (match[1] ?? match[2])
    .split(',')
    .map((path) => path.trim())
    .filter(Boolean);
}

const URL_SCHEME = /^([a-z][a-z0-9+.-]*):\/\//i;

/**
 * The problems with one cited source. A repository path must exist, be public and not be
 * git-ignored; an https:// URL (a competitor's documentation, for a migration page) is not
 * checked, and any other scheme is refused.
 * @param {string} source
 * @param {{ exists: (path: string) => boolean, isPrivate: (path: string) => boolean, isIgnored: (path: string) => boolean | null }} deps
 * @returns {string[]}
 */
export function checkSource(source, deps) {
  const scheme = URL_SCHEME.exec(source)?.[1].toLowerCase();
  if (scheme === 'https') return [];
  if (scheme) return [`cites ${source}, but only https:// URLs may be cited`];
  const problems = [];
  if (!deps.exists(source)) problems.push(`cites missing path ${source}`);
  if (deps.isPrivate(source)) problems.push(`cites private path ${source}`);
  if (deps.isIgnored(source)) problems.push(`cites git-ignored path ${source}`);
  return problems;
}

// Every spelling of "install these packages" that the docs might use. A bare `npm install` names
// no package, so it passes. The verb must start a word (or follow a shell operator).
const INSTALL_VERB =
  /(?:^|[\s;&|(])((?:uv )?pip3? install|uv add|npm (?:install|add|i)|pnpm (?:add|install|i)|yarn add|bun add)(?=\s|$)/g;
// Where one command ends and the next begins on a line.
const COMMAND_END = /&&|\|\||;|\||>/;
const FLAGS_WITH_VALUE = new Set(['-r', '-e', '-c', '-i', '--requirement', '--editable', '--constraint', '--index-url', '--extra-index-url']);

// pip compares names case-insensitively and treats - _ . as one separator.
const normalizeName = (name) => name.toLowerCase().replace(/[-_.]+/g, '-');

/** [{ name, version | null }] for the packages on an install line (flags, paths and shell syntax skipped). */
function installedPackages(command, rest) {
  const tokens = rest.replace(/["']/g, '').split(/\s+/).filter(Boolean);
  const packages = [];
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (token === '\\') continue;
    if (token.startsWith('-')) {
      if (FLAGS_WITH_VALUE.has(token)) i += 1;
      continue;
    }
    if (/^[./~]/.test(token)) continue;
    if (/^(npm|pnpm|yarn|bun)\b/.test(command)) {
      const at = token.lastIndexOf('@');
      packages.push(at > 0 ? { name: token.slice(0, at), version: token.slice(at + 1) } : { name: token, version: null });
    } else {
      const match = /^([^\s=<>~!\[]+)(?:\[[^\]]*\])?(?:==(\S+))?(?:[<>~!=].*)?$/.exec(token);
      if (match) packages.push({ name: match[1], version: match[2] ?? null });
    }
  }
  return packages;
}

/**
 * Problems with the install commands in a page's code fences: every package must be pinned
 * (`==X` for pip and uv, `@X` for npm and pnpm) to the version `<Verified>` states for it. A package
 * published to both PyPI and npm is listed as "name (Python)" and "name (npm)".
 * `checked` is how many packages were looked at, so a caller can tell "all pinned" from "nothing found".
 * @param {string} markdown
 * @returns {{ problems: string[], checked: number }}
 */
export function scanInstallPins(markdown) {
  const verified = new Map((verifiedBlock(markdown)?.packages ?? []).map((pkg) => [normalizeName(pkg.name), pkg.version]));
  const problems = [];
  let checked = 0;
  for (const fence of markdown.matchAll(/^[ \t]*(```|~~~)[^\n]*\n([\s\S]*?)^[ \t]*\1/gm)) {
    // A trailing backslash continues the command on the next line.
    for (const line of fence[2].replace(/\\[ \t]*\r?\n/g, ' ').split('\n')) {
      for (const verb of line.matchAll(INSTALL_VERB)) {
        const rest = line.slice(verb.index + verb[0].length);
        const end = rest.search(COMMAND_END);
        const segment = (end === -1 ? rest : rest.slice(0, end)).trim();
        const command = `${verb[1]} ${segment}`.replace(/\s+/g, ' ').trim();
        // The same package name can exist on PyPI and npm (openai), at different versions. A page
        // lists those as "openai (Python)" and "openai (npm)"; a plain name still matches either.
        const ecosystem = /^(npm|pnpm|yarn|bun)\b/.test(verb[1]) ? 'npm' : 'python';
        for (const { name, version } of installedPackages(verb[1], segment)) {
          checked += 1;
          const where = `install line "${command}": ${name}`;
          const key = normalizeName(name);
          const stated = verified.get(`${key} (${ecosystem})`) ?? verified.get(key);
          if (version === null) problems.push(`${where} is not pinned`);
          else if (stated === undefined) problems.push(`${where} is not listed in <Verified>`);
          else if (stated !== version) problems.push(`${where} is pinned to ${version}, but <Verified> says ${stated}`);
        }
      }
    }
  }
  return { problems, checked };
}

/**
 * The problems found by {@link scanInstallPins}.
 * @param {string} markdown
 * @returns {string[]}
 */
export function installPinProblems(markdown) {
  return scanInstallPins(markdown).problems;
}
