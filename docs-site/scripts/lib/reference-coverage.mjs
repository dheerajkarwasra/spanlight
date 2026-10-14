// Checks that the reference pages document everything the code exposes: every name in the
// Python SDK's `__all__` and every command of the admin CLI. The code is the source of truth,
// so a new export or command fails the test until its page mentions it.

import fs from 'node:fs';
import path from 'node:path';

/**
 * The names in `__all__` of the SDK's `__init__.py`, `__version__` included.
 * @param {string} initPySource
 * @returns {string[]}
 */
export function pythonExports(initPySource) {
  const block = /__all__\s*=\s*\[([\s\S]*?)\]/.exec(initPySource);
  if (!block) return [];
  return [...block[1].matchAll(/["']([^"']+)["']/g)].map((match) => match[1]);
}

/**
 * The CLI modules of a directory: every `cli*.py` file, sorted, as full paths.
 * @param {string} dir
 * @returns {string[]}
 */
export function cliSourceFiles(dir) {
  return fs
    .readdirSync(dir)
    .filter((name) => /^cli.*\.py$/.test(name))
    .sort()
    .map((name) => path.join(dir, name));
}

const QUOTED = '["\']([^"\']+)["\']';

/** Net depth of round brackets in a line, ignoring quoted text and a trailing comment. */
function bracketDepth(line) {
  let depth = 0;
  let quote = null;
  for (const char of line) {
    if (quote) {
      if (char === quote) quote = null;
    } else if (char === '"' || char === "'") quote = char;
    else if (char === '#') break;
    else if (char === '(') depth += 1;
    else if (char === ')') depth -= 1;
  }
  return depth;
}

/**
 * The command decorators of one source, as `{ app, args, func }`. A decorator starts at the
 * beginning of a line (`@<app>.command(`) and may span lines. Its function is the next `def`,
 * with blank lines, comment lines and other decorators allowed in between; another command
 * decorator before any `def` ends the search (`func` is then null).
 */
function commandDecorators(source) {
  const found = [];
  let pending = null;
  let open = 0;
  for (const line of source.split('\n')) {
    if (pending && open > 0) {
      pending.args += `\n${line}`;
      open += bracketDepth(line);
      continue;
    }
    const start = /^@(\w+)\.command\((.*)$/.exec(line);
    if (start) {
      pending = { app: start[1], args: start[2], func: null };
      found.push(pending);
      open = 1 + bracketDepth(start[2]);
      continue;
    }
    const def = /^(?:async[ \t]+)?def[ \t]+(\w+)/.exec(line);
    if (def && pending) {
      pending.func = def[1];
      pending = null;
    }
  }
  return found;
}

/**
 * Every command of the CLI sources: the `@<app>.command(...)` decorators at the start of a
 * line. The name is the first string argument, a `name="..."` keyword, or, with neither, the
 * function name with underscores turned into dashes (Typer's rule). A command on a sub-app is
 * prefixed with the group it is mounted under by `add_typer(<app>, ..., name="group")`,
 * wherever that call is.
 * @param {string[]} cliSources
 * @returns {string[]}
 */
export function cliCommands(cliSources) {
  const groups = new Map();
  for (const source of cliSources) {
    for (const match of source.matchAll(/add_typer\(\s*(\w+)\b([^)]*)\)/g)) {
      const name = new RegExp(`\\bname\\s*=\\s*${QUOTED}`).exec(match[2]);
      if (name) groups.set(match[1], name[1]);
    }
  }
  const first = new RegExp(`^\\s*${QUOTED}`);
  const keyword = new RegExp(`\\bname\\s*=\\s*${QUOTED}`);
  const commands = [];
  for (const source of cliSources) {
    for (const { app, args, func } of commandDecorators(source)) {
      const given = (first.exec(args) ?? keyword.exec(args))?.[1];
      const name = given ?? func?.replaceAll('_', '-');
      if (name) commands.push(groups.has(app) ? `${groups.get(app)} ${name}` : name);
    }
  }
  return commands;
}

/** The text of the page's inline code spans, with fenced code blocks left out. */
function inlineCode(page) {
  const withoutFences = page.replace(/^(`{3,}|~{3,})[^\n]*\n[\s\S]*?^\1[ \t]*$/gm, '');
  return [...withoutFences.matchAll(/`([^`\n]+)`/g)].map((match) => match[1]);
}

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * The names that no inline code span of the page contains as a whole word (a name is not
 * found inside a longer one: `reset` is not in `reset-2fa`).
 * @param {string} page
 * @param {string[]} names
 * @returns {string[]}
 */
export function missingFrom(page, names) {
  const spans = inlineCode(page);
  return names.filter((name) => {
    const pattern = new RegExp(`(^|[^\\w-])${escapeRegExp(name)}($|[^\\w-])`);
    return !spans.some((span) => pattern.test(span));
  });
}

/**
 * The names in the `### \`name\`` headings of a page, outside code fences, in page order.
 * @param {string} page
 * @returns {string[]}
 */
export function headingNames(page) {
  const names = [];
  let fence = null;
  for (const line of page.split('\n')) {
    const opening = line.match(/^\s*(`{3,}|~{3,})/);
    if (fence) {
      if (opening && opening[1][0] === fence[0] && opening[1].length >= fence.length) fence = null;
      continue;
    }
    if (opening) {
      fence = opening[1];
      continue;
    }
    const heading = /^### `([^`]+)`\s*$/.exec(line);
    if (heading) names.push(heading[1]);
  }
  return names;
}

/**
 * The names that are neither in the known set nor in the allowlist.
 * @param {string[]} names
 * @param {string[]} known
 * @param {string[]} [allowed]
 * @returns {string[]}
 */
export function unknownNames(names, known, allowed = []) {
  const ok = new Set([...known, ...allowed]);
  return names.filter((name) => !ok.has(name));
}

/**
 * The right-hand side of a module-level `NAME = ...` (or `NAME: type = ...`) line, trimmed, with
 * any trailing comment kept; null when there is no such line. Indented assignments are not
 * module level and are ignored.
 * @param {string} source
 * @param {string} name
 * @returns {string | null}
 */
export function pythonConstant(source, name) {
  const pattern = new RegExp(`^${escapeRegExp(name)}[ \\t]*(?::[^=\\n]+)?=[ \\t]*(.+)$`, 'm');
  return pattern.exec(source)?.[1].trim() ?? null;
}

/**
 * The values of `class Permission(enum.StrEnum)`, in order: the strings assigned to its members.
 * @param {string} permissionsSource
 * @returns {string[]}
 */
export function permissionNames(permissionsSource) {
  const start = /^class Permission\(enum\.StrEnum\):[ \t]*$/m.exec(permissionsSource);
  if (!start) return [];
  const body = permissionsSource.slice(start.index + start[0].length).split(/^(?=\S)/m)[0];
  return [...body.matchAll(/^[ \t]+\w+[ \t]*=[ \t]*["']([^"']+)["']/gm)].map((match) => match[1]);
}

/**
 * The error codes in the first column of the page's tables: UPPER_SNAKE names in an inline code
 * span that is the whole first cell of a table row.
 * @param {string} page
 * @returns {string[]}
 */
export function errorCodesIn(page) {
  return [...page.matchAll(/^\|[ \t]*`([A-Z][A-Z0-9_]*)`[ \t]*\|/gm)].map((match) => match[1]);
}

/**
 * Whether the code appears as a quoted string literal in any of the backend sources, so a page
 * cannot list a code the server never sends.
 * @param {string} code
 * @param {string[]} backendSources
 * @returns {boolean}
 */
export function codeExistsInBackend(code, backendSources) {
  return backendSources.some((source) => source.includes(`"${code}"`));
}

/** The `NAME = "value"` members of the enum class `className`, as `{ NAME: value }`. */
function enumMembers(source, className) {
  const start = new RegExp(`^class ${className}\\(enum\\.StrEnum\\):[ \\t]*$`, 'm').exec(source);
  if (!start) return {};
  const body = source.slice(start.index + start[0].length).split(/^(?=\S)/m)[0];
  const members = {};
  for (const match of body.matchAll(/^[ \t]+(\w+)[ \t]*=[ \t]*["']([^"']+)["']/gm)) {
    members[match[1]] = match[2];
  }
  return members;
}

/**
 * The permission set of each role, from the code: the module-level `_NAME = ...` sets (unions
 * written with `|`, brace sets, `frozenset(...)`) and the `ROLE_PERMISSIONS` dict that maps
 * `MembershipRole.X` to one of them. Every operator in those expressions is a union, so an
 * expression is the permissions it names plus the sets it refers to.
 * @param {string} permissionsSource
 * @returns {Record<string, Set<string>>}
 */
export function rolePermissions(permissionsSource) {
  const values = enumMembers(permissionsSource, 'Permission');
  const sets = new Map();
  const expression = (text) => {
    const set = new Set();
    for (const match of text.matchAll(/Permission\.(\w+)|\b(_[A-Z]+)\b/g)) {
      if (match[1]) set.add(values[match[1]]);
      else for (const value of sets.get(match[2]) ?? []) set.add(value);
    }
    return set;
  };
  for (const match of permissionsSource.matchAll(/^(_[A-Z]+)[ \t]*=[ \t]*([\s\S]*?)(?=^\S)/gm)) {
    sets.set(match[1], expression(match[2]));
  }
  const roles = {};
  const table = /^ROLE_PERMISSIONS[^=]*=\s*\{([\s\S]*?)^\}/m.exec(permissionsSource);
  for (const match of (table?.[1] ?? '').matchAll(/MembershipRole\.(\w+):\s*([^\n]+)/g)) {
    roles[match[1].toLowerCase()] = expression(match[2]);
  }
  return roles;
}

const cells = (line) =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());

/** The rows of the first table whose header has `matches` true, as arrays of cells. */
function tableRows(page, matches) {
  const lines = page.split('\n');
  const start = lines.findIndex((line) => line.startsWith('|') && matches(cells(line)));
  if (start === -1) return { header: [], rows: [] };
  const rows = [];
  for (const line of lines.slice(start + 1)) {
    if (!line.startsWith('|')) break;
    if (/^\|[\s:|-]+\|?$/.test(line)) continue;
    rows.push(cells(line));
  }
  return { header: cells(lines[start]), rows };
}

const codeCell = (cell) => /^`([^`]+)`$/.exec(cell)?.[1] ?? null;

/**
 * The roles matrix of a page: the table whose header names Viewer, Member, Admin and Owner.
 * For each role, the set of permissions (inline code in the first cell) whose cell says Yes,
 * with the roles read in the header's own column order.
 * @param {string} page
 * @returns {Record<string, Set<string>>}
 */
export function roleMatrixFromPage(page) {
  const { header, rows } = tableRows(page, (head) =>
    ['viewer', 'member', 'admin', 'owner'].every((role) =>
      head.some((cell) => cell.toLowerCase() === role),
    ),
  );
  const matrix = {};
  header.forEach((cell, index) => {
    const role = cell.toLowerCase();
    if (!['viewer', 'member', 'admin', 'owner'].includes(role)) return;
    matrix[role] = new Set(
      rows.filter((row) => codeCell(row[0]) && row[index] === 'Yes').map((row) => codeCell(row[0])),
    );
  });
  return matrix;
}

/**
 * The values of `class KeyScope(enum.StrEnum)`, in order.
 * @param {string} scopesSource
 * @returns {string[]}
 */
export function keyScopes(scopesSource) {
  return Object.values(enumMembers(scopesSource, 'KeyScope'));
}

/**
 * The inline-code first cells of the table whose first header cell is `firstHeader`.
 * @param {string} page
 * @param {string} firstHeader
 * @returns {string[]}
 */
export function tableFirstCells(page, firstHeader) {
  const { rows } = tableRows(page, (head) => head[0] === firstHeader);
  return rows.map((row) => codeCell(row[0])).filter((code) => code !== null);
}

/**
 * The code rows of the page's tables: the code in the first cell and every three-digit HTTP
 * status in the second cell.
 * @param {string} page
 * @returns {{ code: string, statuses: number[] }[]}
 */
export function errorRowsIn(page) {
  return [...page.matchAll(/^\|[ \t]*`([A-Z][A-Z0-9_]*)`[ \t]*\|([^|\n]*)\|/gm)].map((match) => ({
    code: match[1],
    statuses: [...match[2].matchAll(/\b\d{3}\b/g)].map((status) => Number(status[0])),
  }));
}

/**
 * The HTTP statuses the backend answers `code` with, sorted: `ProblemError(<status>, "CODE"` and
 * `problem_response(<status>, "CODE"`, `conflict("CODE"` (always 409), and the `<status>: "CODE"`
 * entries of the status-to-code map.
 * @param {string} code
 * @param {string[]} backendSources
 * @returns {number[]}
 */
export function statusesInCode(code, backendSources) {
  const name = escapeRegExp(code);
  const patterns = [
    new RegExp(`\\b(?:ProblemError|problem_response)\\(\\s*(\\d{3})\\s*,\\s*"${name}"`, 'g'),
    new RegExp(`\\b(\\d{3}):\\s*"${name}"`, 'g'),
  ];
  const found = new Set();
  for (const source of backendSources) {
    for (const pattern of patterns) {
      for (const match of source.matchAll(pattern)) found.add(Number(match[1]));
    }
    if (new RegExp(`\\bconflict\\(\\s*"${name}"`).test(source)) found.add(409);
  }
  return [...found].sort((a, b) => a - b);
}

/**
 * Every `gen_ai.…` attribute key that the OTLP mapper's sources mention as a quoted string, in
 * order of first appearance. A bare `"gen_ai."` prefix test is not a key and is skipped.
 * @param {string[]} otlpSources
 * @returns {string[]}
 */
export function otlpKeysIn(otlpSources) {
  const keys = new Set();
  for (const source of otlpSources) {
    for (const match of source.matchAll(/["'](gen_ai\.[A-Za-z0-9_.]*[A-Za-z0-9_])["']/g)) keys.add(match[1]);
  }
  return [...keys];
}

/**
 * The Python files of a directory, sorted, as full paths.
 * @param {string} dir
 * @returns {string[]}
 */
export function ingestSourceFiles(dir) {
  return fs
    .readdirSync(dir)
    .filter((name) => name.endsWith('.py'))
    .sort()
    .map((name) => path.join(dir, name));
}

const quotedStrings = (text) => [...text.matchAll(/["']([^"']+)["']/g)].map((match) => match[1]);

/**
 * The attribute keys, other than the `gen_ai.*` ones, that the OTLP mapper looks up: the string
 * arguments of `first(...)`, the keys of `attributes.get(...)` and `resource.get(...)`, the names in `STATUS_ATTRIBUTES` and in `_ATTRIBUTE_SOURCES`, and the
 * prefixes tested with `startswith(...)` (they end in a dot). Sorted, without duplicates.
 * @param {string[]} otlpSources
 * @returns {string[]}
 */
export function otlpLookupKeys(otlpSources) {
  const keys = new Set();
  for (const source of otlpSources) {
    for (const match of source.matchAll(/\bfirst\(([^)]*)\)/g)) {
      for (const key of quotedStrings(match[1])) if (!key.startsWith('gen_ai.')) keys.add(key);
    }
    for (const match of source.matchAll(/\b(?:attributes|resource)\.get\(\s*["']([^"']+)["']/g)) {
      if (!match[1].startsWith('gen_ai.')) keys.add(match[1]);
    }
    for (const match of source.matchAll(/\bstartswith\(([^)]*)\)/g)) {
      for (const prefix of quotedStrings(match[1])) if (prefix.endsWith('.')) keys.add(prefix);
    }
    const statusAttributes = /^STATUS_ATTRIBUTES\s*=\s*\(([^)]*)\)/m.exec(source);
    if (statusAttributes) for (const key of quotedStrings(statusAttributes[1])) keys.add(key);
    const sources = /^_ATTRIBUTE_SOURCES\s*=\s*\(([\s\S]*?)^\)/m.exec(source);
    if (sources) {
      for (const match of sources[1].matchAll(/\(\s*["']([^"']+)["']\s*,\s*(?:True|False)\s*\)/g)) {
        if (!match[1].startsWith('gen_ai.')) keys.add(match[1]);
      }
    }
  }
  return [...keys].sort();
}

/**
 * The cells of the rows of the first table whose header (a row followed by a separator line) has
 * `firstHeader` as its first cell. A body row that starts with the same word is not a header.
 */
function rowsOf(page, firstHeader) {
  const lines = page.split('\n');
  const start = lines.findIndex(
    (line, index) =>
      line.startsWith('|') &&
      cells(line)[0] === firstHeader &&
      /^\|[\s:|-]+\|?$/.test(lines[index + 1] ?? ''),
  );
  if (start === -1) return [];
  const rows = [];
  for (const line of lines.slice(start + 2)) {
    if (!line.startsWith('|')) break;
    rows.push(cells(line));
  }
  return rows;
}

const codesIn = (cell) => [...cell.matchAll(/`([^`]+)`/g)].map((match) => match[1]);

/** Differences between two `{ key: value }` maps, as messages naming the source of each side. */
function mapDrift(label, fromCode, fromPage) {
  const problems = [];
  for (const [key, value] of Object.entries(fromCode)) {
    if (!(key in fromPage)) problems.push(`${label}: ${key} is in the code but not on the page`);
    else if (fromPage[key] !== value) {
      problems.push(`${label}: ${key} is ${value} in the code but ${fromPage[key]} on the page`);
    }
  }
  for (const key of Object.keys(fromPage)) {
    if (!(key in fromCode)) problems.push(`${label}: ${key} is on the page but not in the code`);
  }
  return problems;
}

/**
 * Differences between `_CANONICAL` (finish_reason.py) and the "Raw value | Stored value" table:
 * every raw value must be in a row (as inline code) with its stored value, and the page must list
 * no raw value the code does not map.
 * @param {string} finishReasonSource
 * @param {string} page
 * @returns {string[]}
 */
export function finishReasonDrift(finishReasonSource, page) {
  const body = /^_CANONICAL\s*=\s*\{([\s\S]*?)^\}/m.exec(finishReasonSource)?.[1] ?? '';
  const fromCode = {};
  for (const match of body.matchAll(/["']([^"']+)["']\s*:\s*["']([^"']+)["']/g)) {
    fromCode[match[1]] = match[2];
  }
  const fromPage = {};
  for (const row of rowsOf(page, 'Raw value')) {
    const stored = codeCell(row[1] ?? '');
    for (const raw of codesIn(row[0])) fromPage[raw] = stored;
  }
  return mapDrift('finish reason', fromCode, fromPage);
}

/** `ErrorClass` member name to its value, from error_class.py. */
function errorClassValues(errorClassSource) {
  return enumMembers(errorClassSource, 'ErrorClass');
}

/**
 * Differences between `_STATUS_CLASSES` (error_class.py) and the "Status | Class" table: each
 * listed status (a row whose first cell is only three-digit numbers) maps to the same class on the
 * page, and the page lists none the code does not. The two range rules (any other 5xx, any other
 * 4xx) must still be in the code and on the page with their classes.
 * @param {string} errorClassSource
 * @param {string} page
 * @returns {string[]}
 */
export function statusClassDrift(errorClassSource, page) {
  const values = errorClassValues(errorClassSource);
  const body = /^_STATUS_CLASSES[^=\n]*=\s*\{([\s\S]*?)^\}/m.exec(errorClassSource)?.[1] ?? '';
  const fromCode = {};
  for (const match of body.matchAll(/(\d{3})\s*:\s*ErrorClass\.(\w+)/g)) {
    fromCode[match[1]] = values[match[2]];
  }
  const fromPage = {};
  const rows = rowsOf(page, 'Status');
  for (const row of rows) {
    if (!/^\d{3}(, \d{3})*$/.test(row[0])) continue;
    for (const status of row[0].split(', ')) fromPage[status] = codeCell(row[1] ?? '');
  }
  const problems = mapDrift('status', fromCode, fromPage);
  const ranges = [
    [/500\s*<=\s*code\s*<=\s*599:\s*return ErrorClass\.PROVIDER_5XX/, /500 to 599/, 'provider_5xx'],
    [/400\s*<=\s*code\s*<=\s*499:\s*return ErrorClass\.CLIENT/, /400 to 499/, 'client'],
  ];
  for (const [codePattern, label, value] of ranges) {
    if (!codePattern.test(errorClassSource)) problems.push(`status: the ${value} range rule is not in the code`);
    const row = rows.find((cells) => label.test(cells[0]));
    if (!row || codesIn(row[1] ?? '')[0] !== value) problems.push(`status: the ${value} range row is missing on the page`);
  }
  return problems;
}

/**
 * Differences between `_MESSAGE_PATTERNS` (error_class.py) and the "Class | Phrases" table: the
 * same classes in the same order, each with exactly the phrases of the code (inline code).
 * @param {string} errorClassSource
 * @param {string} page
 * @returns {string[]}
 */
export function messagePhraseDrift(errorClassSource, page) {
  const values = errorClassValues(errorClassSource);
  const body = /^_MESSAGE_PATTERNS[^=\n]*=\s*\(([\s\S]*?)^\)/m.exec(errorClassSource)?.[1] ?? '';
  const fromCode = [...body.matchAll(/ErrorClass\.(\w+),\s*\(([^)]*)\)/g)].map((match) => ({
    name: values[match[1]],
    phrases: quotedStrings(match[2]),
  }));
  const fromPage = rowsOf(page, 'Class').map((row) => ({
    name: codeCell(row[0]),
    phrases: codesIn(row[1] ?? ''),
  }));
  const problems = [];
  const order = (rows) => rows.map((row) => row.name).join(' > ');
  if (order(fromCode) !== order(fromPage)) {
    problems.push(`phrases: classes are ${order(fromCode)} in the code but ${order(fromPage)} on the page`);
  }
  for (const { name, phrases } of fromCode) {
    const onPage = fromPage.find((row) => row.name === name)?.phrases ?? [];
    for (const phrase of phrases) {
      if (!onPage.includes(phrase)) problems.push(`phrases: "${phrase}" (${name}) is not on the page`);
    }
    for (const phrase of onPage) {
      if (!phrases.includes(phrase)) problems.push(`phrases: "${phrase}" (${name}) is on the page but not in the code`);
    }
  }
  return problems;
}
