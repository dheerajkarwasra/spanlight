import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  cliCommands,
  cliSourceFiles,
  codeExistsInBackend,
  errorCodesIn,
  errorRowsIn,
  keyScopes,
  finishReasonDrift,
  headingNames,
  unknownNames,
  ingestSourceFiles,
  messagePhraseDrift,
  otlpKeysIn,
  otlpLookupKeys,
  statusClassDrift,
  roleMatrixFromPage,
  rolePermissions,
  statusesInCode,
  tableFirstCells,
  missingFrom,
  permissionNames,
  pythonConstant,
  pythonExports,
} from './reference-coverage.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '../../..');
const DOCS = path.resolve(HERE, '../../src/content/docs');

const readRepo = (relative) => fs.readFileSync(path.join(REPO_ROOT, relative), 'utf8');
const readPage = (relative) => fs.readFileSync(path.join(DOCS, relative), 'utf8');

const initSource = () => readRepo('sdks/python/src/spanlight/__init__.py');
const CLI_DIR = path.join(REPO_ROOT, 'backend/app');
const cliSources = () => cliSourceFiles(CLI_DIR).map((file) => fs.readFileSync(file, 'utf8'));

test('reads __all__ from the SDK', () => {
  const names = pythonExports(initSource());
  for (const expected of ['Spanlight', 'init', 'observe', 'wrap_openai', 'SpanStatus']) {
    assert.ok(names.includes(expected), `${expected} is missing from ${names.join(', ')}`);
  }
  assert.ok(names.includes('__version__'), '__version__ is a public name and the page documents it');
});

test('reads CLI commands with their groups', () => {
  const commands = cliCommands(cliSources());
  for (const expected of [
    'create-user',
    'backup now',
    'rollups backfill',
    'demo seed-evals',
    'alerts evaluate',
  ]) {
    assert.ok(commands.includes(expected), `${expected} is missing from ${commands.join(', ')}`);
  }
});

test('missingFrom counts a name only when it sits in an inline code span', () => {
  const page = 'Use `init` and `spanlight backup now`.\n\n```python\nobserve()\n```\n\nPlain observe text.';
  assert.deepEqual(missingFrom(page, ['init', 'backup now', 'observe', 'flush']), ['observe', 'flush']);
});

test('missingFrom does not match part of a longer name', () => {
  assert.deepEqual(missingFrom('`wrap_openai`', ['wrap_open', 'wrap_openai']), ['wrap_open']);
  assert.deepEqual(missingFrom('`reset-2fa`', ['reset']), ['reset']);
});

test('every SDK export is documented', () => {
  assert.deepEqual(missingFrom(readPage('reference/python-sdk.mdx'), pythonExports(initSource())), []);
});

test('every CLI command is documented', () => {
  assert.deepEqual(missingFrom(readPage('reference/cli.mdx'), cliCommands(cliSources())), []);
});

test('headingNames reads the code-span ### headings and ignores the others', () => {
  const page = '### `a`\n\ntext\n\n### Plain heading\n\n### `b c`\n\n## `h2`\n\n```\n### `fenced`\n```\n';
  assert.deepEqual(headingNames(page), ['a', 'b c']);
});

test('unknownNames lists the names that are not in the known set, minus the allowlist', () => {
  assert.deepEqual(unknownNames(['a', 'b', 'c'], ['a'], ['c']), ['b']);
});

// The `### \`name\`` headings of the SDK reference that are not names in `__all__`, each with the
// reason it is there. Everything else must be an export, so a removed or renamed export fails here.
const SDK_HEADING_ALLOWLIST = [
  // A keyword argument that wrapped provider calls accept, not an export.
  'spanlight_prompt',
];

test('every CLI heading is a command in the code', () => {
  const headings = headingNames(readPage('reference/cli.mdx')).map((name) => name.replace(/^spanlight /, ''));
  assert.ok(headings.length >= 13, `found only ${headings.join(', ')}`);
  assert.deepEqual(unknownNames(headings, cliCommands(cliSources())), []);
});

test('the CLI page catches a command that was renamed in the page', () => {
  const page = readPage('reference/cli.mdx').replace('### `reset-2fa`', '### `reset-two-factor`');
  const headings = headingNames(page).map((name) => name.replace(/^spanlight /, ''));
  assert.notDeepEqual(unknownNames(headings, cliCommands(cliSources())), []);
});

test('every SDK heading is an export, except the listed non-exports', () => {
  const headings = headingNames(readPage('reference/python-sdk.mdx'));
  assert.ok(headings.length >= 20, `found only ${headings.join(', ')}`);
  assert.deepEqual(unknownNames(headings, pythonExports(initSource()), SDK_HEADING_ALLOWLIST), []);
});

test('the SDK page catches an export that was renamed in the page', () => {
  const page = readPage('reference/python-sdk.mdx').replace('### `get_current_span`', '### `get_active_span`');
  assert.notDeepEqual(
    unknownNames(headingNames(page), pythonExports(initSource()), SDK_HEADING_ALLOWLIST),
    [],
  );
});

test('finds every backend/app/cli*.py module', () => {
  const expected = fs
    .readdirSync(CLI_DIR)
    .filter((name) => /^cli.*\.py$/.test(name))
    .map((name) => path.join(CLI_DIR, name))
    .sort();
  assert.ok(expected.length >= 3);
  assert.deepEqual(cliSourceFiles(CLI_DIR), expected);
});

test('takes the command name from the function when the decorator has none', () => {
  const source = '@app.command()\ndef backfill_all(project: str) -> None:\n    pass\n';
  assert.deepEqual(cliCommands([source]), ['backfill-all']);
});

test('reads a keyword command name and a group name that is not the second argument', () => {
  const sources = [
    'sub = typer.Typer()\n\n@sub.command(name="run-now")\ndef x() -> None: ...\n\n@sub.command()\ndef do_more() -> None: ...\n',
    'app.add_typer(sub, help="x", name="grp")\n',
  ];
  assert.deepEqual(cliCommands(sources), ['grp run-now', 'grp do-more']);
});

test('ignores commented-out and indented decorators', () => {
  assert.deepEqual(cliCommands(['# @app.command("old")\n    @app.command("nested")\n']), []);
});

test('finds a command whose def is separated by blank, comment and decorator lines', () => {
  const source = [
    '@app.command("a")  # trailing comment',
    '',
    '# a comment line',
    'def a() -> None: ...',
    '',
    '@app.command()',
    '@other.decorator',
    '',
    'async def b_c() -> None: ...',
    '',
  ].join('\n');
  assert.deepEqual(cliCommands([source]), ['a', 'b-c']);
});

test('finds the last command of a file when a blank line precedes its def', () => {
  const source = '@app.command("first")\ndef first() -> None: ...\n\n@app.command()\n\ndef last_one() -> None: ...';
  assert.deepEqual(cliCommands([source]), ['first', 'last-one']);
});

test('reads a decorator that spans several lines', () => {
  const source = '@app.command(\n    "wide",\n    help="x (y)",\n)\ndef wide() -> None: ...\n';
  assert.deepEqual(cliCommands([source]), ['wide']);
});

test('a decorator without a def does not swallow the next command', () => {
  const source = '@app.command("lost")\n\n@app.command("kept")\ndef kept() -> None: ...\n';
  assert.deepEqual(cliCommands([source]), ['lost', 'kept']);
});

test('finds as many commands as there are command decorator lines in the repo', () => {
  const sources = cliSourceFiles(CLI_DIR).map((file) => fs.readFileSync(file, 'utf8'));
  const decorators = sources
    .flatMap((source) => source.split('\n'))
    .filter((line) => /^\s*@\w+\.command\(/.test(line)).length;
  assert.ok(decorators >= 13);
  assert.equal(cliCommands(sources).length, decorators);
});

test('pythonConstant reads the right-hand side of a module-level assignment', () => {
  const source = 'MAX_A = 32 * 1024\nMAX_B: int = 7  # note\n    INDENTED = 1\nMAX_C = timedelta(days=90)  # why\n';
  assert.equal(pythonConstant(source, 'MAX_A'), '32 * 1024');
  assert.equal(pythonConstant(source, 'MAX_B'), '7  # note');
  assert.equal(pythonConstant(source, 'MAX_C'), 'timedelta(days=90)  # why');
  assert.equal(pythonConstant(source, 'INDENTED'), null);
  assert.equal(pythonConstant(source, 'MAX'), null);
});

test('ingest limits in the code still have the documented values', () => {
  const normalize = readRepo('backend/app/ingest/normalize.py');
  assert.equal(pythonConstant(normalize, 'MAX_PAYLOAD_BYTES'), '32 * 1024');
  assert.equal(pythonConstant(normalize, 'MAX_ATTRIBUTES_BYTES'), '64 * 1024');
  assert.ok(pythonConstant(normalize, 'MAX_SPAN_AGE')?.startsWith('timedelta(days=90)'));
  assert.ok(pythonConstant(normalize, 'MAX_CLOCK_SKEW')?.startsWith('timedelta(minutes=10)'));
  const schemas = readRepo('backend/app/ingest/schemas.py');
  assert.equal(pythonConstant(schemas, 'MAX_SPANS_PER_BATCH'), '1000');
  assert.equal(pythonConstant(schemas, 'MAX_BODY_BYTES'), '5 * 1024 * 1024');
});

test('the limits page states each of those values', () => {
  const page = readPage('reference/limits.mdx');
  for (const value of ['32 KB', '64 KB', '90 days', '10 minutes', '1,000 spans', '5 MB']) {
    assert.ok(page.includes(value), `limits page does not state ${value}`);
  }
});

test('the score config cap in the code is the documented 500', () => {
  assert.equal(pythonConstant(readRepo('backend/app/scores/queries.py'), 'MAX_CONFIGS'), '500');
  const page = readPage('reference/limits.mdx');
  assert.match(page, /^\| Score configs \(distinct score names\) in a project \| 500 \|/m);
});

test('reads the permission names from the Permission enum', () => {
  const source = 'class Other(enum.StrEnum):\n    X = "x:no"\n\n\nclass Permission(enum.StrEnum):\n    A_B = "a:b"\n    C = "c:d"\n\n\nPAIR = 1\n';
  assert.deepEqual(permissionNames(source), ['a:b', 'c:d']);
  const real = permissionNames(readRepo('backend/app/core/permissions.py'));
  assert.ok(real.includes('org:read') && real.includes('credentials:manage'));
});

test('every permission appears in the roles matrix', () => {
  const names = permissionNames(readRepo('backend/app/core/permissions.py'));
  assert.ok(names.length >= 20);
  assert.deepEqual(missingFrom(readPage('reference/roles.mdx'), names), []);
});

test('reads the error codes from the first column of the code table', () => {
  const page = '| Code | Status |\n| --- | --- |\n| `NOT_FOUND` | 404 |\n| `RATE_LIMITED` | 429 |\n\nUse `OTHER_CODE` here. | `NOT_A_CELL` | x |\n';
  assert.deepEqual(errorCodesIn(page), ['NOT_FOUND', 'RATE_LIMITED']);
});

test('codeExistsInBackend needs the code as a string literal', () => {
  assert.equal(codeExistsInBackend('NOT_FOUND', ['x = "NOT_FOUND"']), true);
  assert.equal(codeExistsInBackend('NOT_FOUND', ['# NOT_FOUND_LATER\ny = "NOT_FOUND_LATER"']), false);
});

/** Every `.py` file under a directory, as source text. */
function pythonSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === '__pycache__' ? [] : pythonSources(full);
    return entry.name.endsWith('.py') ? [fs.readFileSync(full, 'utf8')] : [];
  });
}

test('every error code on the errors page exists in the backend', () => {
  const codes = errorCodesIn(readPage('reference/errors.mdx'));
  for (const expected of [
    'VALIDATION_ERROR',
    'KEY_SCOPE',
    'KEY_EXPIRED',
    'NOT_FOUND',
    'RATE_LIMITED',
    'PAYLOAD_TOO_LARGE',
    'IDEMPOTENCY_MISMATCH',
  ]) {
    assert.ok(codes.includes(expected), `${expected} is missing from the errors page`);
  }
  const sources = pythonSources(path.join(REPO_ROOT, 'backend/app'));
  const invented = codes.filter((code) => !codeExistsInBackend(code, sources));
  assert.deepEqual(invented, []);
});

const PERMISSIONS_SOURCE = () => readRepo('backend/app/core/permissions.py');

test('rolePermissions follows the cumulative unions in the code', () => {
  const fixture = [
    'class Permission(enum.StrEnum):',
    '    A = "a:one"',
    '    B = "b:two"',
    '    C = "c:three"',
    '',
    '',
    '_LOW = frozenset({Permission.A})',
    '_MID = _LOW | {',
    '    Permission.B,',
    '}',
    '_TOP = _MID | {Permission.C}',
    '',
    'ROLE_PERMISSIONS = {',
    '    MembershipRole.VIEWER: _LOW,',
    '    MembershipRole.OWNER: frozenset(_TOP),',
    '}',
    '',
  ].join('\n');
  assert.deepEqual(rolePermissions(fixture), {
    viewer: new Set(['a:one']),
    owner: new Set(['a:one', 'b:two', 'c:three']),
  });
  const real = rolePermissions(PERMISSIONS_SOURCE());
  assert.deepEqual(Object.keys(real).sort(), ['admin', 'member', 'owner', 'viewer']);
  assert.ok(real.owner.has('credentials:manage') && !real.admin.has('credentials:manage'));
  assert.ok(real.admin.has('org:read'));
});

test('roleMatrixFromPage reads the Yes cells by the table header order', () => {
  const page = [
    '| Permission | Allows | Viewer | Member | Admin | Owner |',
    '| --- | --- | --- | --- | --- | --- |',
    '| `a:one` | x | Yes | Yes | Yes | Yes |',
    '| `b:two` | y | No | No | Yes | Yes |',
    '',
    '| Scope | Allows |',
    '| `a:one` | z |',
  ].join('\n');
  assert.deepEqual(roleMatrixFromPage(page), {
    viewer: new Set(['a:one']),
    member: new Set(['a:one']),
    admin: new Set(['a:one', 'b:two']),
    owner: new Set(['a:one', 'b:two']),
  });
});

test('the roles matrix on the page matches ROLE_PERMISSIONS for every role', () => {
  assert.deepEqual(
    roleMatrixFromPage(readPage('reference/roles.mdx')),
    rolePermissions(PERMISSIONS_SOURCE()),
  );
});

test('the roles matrix catches a permission moved to another role', () => {
  const page = readPage('reference/roles.mdx').replace(
    /^(\| `project:delete` \|.*\| )No \| No \| Yes \| Yes \|$/m,
    '$1No | No | No | Yes |',
  );
  assert.notDeepEqual(roleMatrixFromPage(page), rolePermissions(PERMISSIONS_SOURCE()));
});

test('reads the API key scopes from KeyScope', () => {
  assert.deepEqual(keyScopes(readRepo('backend/app/core/scopes.py')), [
    'ingest:write',
    'traces:read',
    'scores:write',
    'prompts:read',
  ]);
});

test('tableFirstCells reads one table by its first header cell', () => {
  const page = '| Scope | Allows |\n| --- | --- |\n| `a:b` | x |\n| `c:d` | y |\n\n| Other | Z |\n| --- | --- |\n| `e:f` | q |\n';
  assert.deepEqual(tableFirstCells(page, 'Scope'), ['a:b', 'c:d']);
});

test('every key scope is the first cell of the scopes table, and nothing else is', () => {
  const scopes = keyScopes(readRepo('backend/app/core/scopes.py'));
  assert.deepEqual(tableFirstCells(readPage('reference/roles.mdx'), 'Scope').sort(), [...scopes].sort());
});

test('statusesInCode finds the status of a raise site, a helper and a status map', () => {
  const sources = [
    'raise ProblemError(\n    409,\n    "ONE",\n)',
    'return ProblemError(422, "ONE", "x")',
    'raise conflict("TWO", "x")',
    '413: "THREE",',
    'problem_response(\n 500, "FOUR")',
    'x = "FIVE"',
  ];
  assert.deepEqual(statusesInCode('ONE', sources), [409, 422]);
  assert.deepEqual(statusesInCode('TWO', sources), [409]);
  assert.deepEqual(statusesInCode('THREE', sources), [413]);
  assert.deepEqual(statusesInCode('FOUR', sources), [500]);
  assert.deepEqual(statusesInCode('FIVE', sources), []);
});

test('errorRowsIn reads every status number of a code row', () => {
  const page = '| `A_B` | 401, 422 | m | f |\n| `C_D` | 404 | m | f |\n';
  assert.deepEqual(errorRowsIn(page), [
    { code: 'A_B', statuses: [401, 422] },
    { code: 'C_D', statuses: [404] },
  ]);
});

test('each error code on the errors page lists exactly the statuses the backend raises it with', () => {
  const sources = pythonSources(path.join(REPO_ROOT, 'backend/app'));
  const rows = errorRowsIn(readPage('reference/errors.mdx'));
  const documented = new Map();
  for (const { code, statuses } of rows) {
    documented.set(code, [...(documented.get(code) ?? []), ...statuses]);
  }
  for (const [code, statuses] of documented) {
    const found = statusesInCode(code, sources);
    assert.ok(found.length > 0, `${code}: no raise site with a literal status was found`);
    assert.deepEqual([...new Set(statuses)].sort(), found, `${code}: page and backend statuses differ`);
  }
});

test('the sign-in related limits in the code still have the documented values', () => {
  const reset = readRepo('backend/app/auth/password_reset.py');
  assert.equal(pythonConstant(reset, 'MAX_FORGOT_PER_EMAIL'), '3');
  assert.equal(pythonConstant(reset, 'MAX_FORGOT_PER_IP'), '10');
  assert.equal(pythonConstant(reset, 'FORGOT_WINDOW'), 'timedelta(minutes=15)');
  const totp = readRepo('backend/app/api/v1/totp.py');
  assert.equal(pythonConstant(totp, 'MAX_DISABLE_ATTEMPTS'), '5');
  assert.equal(pythonConstant(totp, 'DISABLE_WINDOW'), 'timedelta(minutes=15)');
  assert.equal(pythonConstant(readRepo('backend/app/prompts/service.py'), 'MAX_VERSIONS_PER_DAY'), '100');
});

test('the limits page lists the password reset, two-factor and prompt version limits', () => {
  const page = readPage('reference/limits.mdx');
  for (const row of [
    /^\| Password reset requests \| 3 in 15 minutes \| Email address \|$/m,
    /^\| Password reset requests \| 10 in 15 minutes \| Client address \|$/m,
    /^\| Attempts to turn off two-factor authentication \| 5 in 15 minutes \| Person \|$/m,
    /^\| New versions of a prompt \| 100 a day \(UTC\) \| Prompt \|$/m,
  ]) {
    assert.match(page, row);
  }
});

test('reads the gen_ai keys the OTLP mapper uses', () => {
  const keys = otlpKeysIn(['x = first("gen_ai.usage.input_tokens", \'gen_ai.system\')\ny = "gen_ai.usage.input_tokens"\nz = "gen_ai."']);
  assert.deepEqual(keys, ['gen_ai.usage.input_tokens', 'gen_ai.system']);
});

const INGEST_DIR = path.join(REPO_ROOT, 'backend/app/ingest');
const ingestSources = () => ingestSourceFiles(INGEST_DIR).map((file) => fs.readFileSync(file, 'utf8'));
const mappingPage = () => readPage('reference/otlp-mapping.mdx');
const lookupSources = ingestSources;

test('lists every Python file of the ingest package', () => {
  const names = ingestSourceFiles(INGEST_DIR).map((file) => path.basename(file));
  for (const expected of ['otlp.py', 'finish_reason.py', 'error_class.py', 'normalize.py']) {
    assert.ok(names.includes(expected), `${expected} is missing from ${names.join(', ')}`);
  }
});

test('every gen_ai key the ingest package reads is documented', () => {
  const keys = otlpKeysIn(ingestSources());
  assert.ok(keys.includes('gen_ai.response.finish_reasons'), 'the finish reasons key is read');
  assert.ok(keys.length >= 10, `found only ${keys.join(', ')}`);
  assert.deepEqual(missingFrom(mappingPage(), keys), []);
});

test('the page documents no gen_ai key the code does not read', () => {
  const read = new Set(otlpKeysIn(ingestSources()));
  const documented = mappingPage().split('## What is not mapped')[0];
  const onPage = [...documented.matchAll(/`(gen_ai\.[\w.]*\w)`/g)].map((match) => match[1]);
  assert.deepEqual(
    onPage.filter((key) => !read.has(key)),
    [],
  );
});

test('reads the other attribute keys and prefixes the mapper looks up', () => {
  const found = otlpLookupKeys([
    'a = first("service.name", "x.y", source=resource)\nb = key.startswith(("http.", "url."))\n' +
      'c = ("finish_reason", False),\nSTATUS_ATTRIBUTES = ("error.status", "http.status_code")\n' +
      '_ATTRIBUTE_SOURCES = (\n    ("response.finish_reasons", True),\n)\n',
  ]);
  assert.deepEqual(found, [
    'error.status',
    'http.status_code',
    'http.',
    'response.finish_reasons',
    'service.name',
    'url.',
    'x.y',
  ].sort());
});

// The attribute keys that are not gen_ai.*, exactly as the code reads them today. When the code
// reads a new one, or stops reading one, this list, the page and the test fail together.
const EXPECTED_LOOKUPS = [
  'db.',
  'deployment.environment',
  'deployment.environment.name',
  'error.status',
  'finish_reason',
  'gen_ai.',
  'http.',
  'http.status_code',
  'response.finish_reasons',
  'response.stop_reason',
  'service.name',
  'service.version',
  'session.id',
  'spanlight.gateway.upstream_status',
  'url.',
  'user.id',
].sort();

test('finds attribute lookups with .get in any file, and not other dictionary lookups', () => {
  const found = otlpLookupKeys([
    'a = first("a.b")',
    'if attributes.get("x.y") is None:\n    pass\nv = span.attributes.get(\'p.q\')\nw = resource.get("r.s")\n' +
      'span.get("input")\nrow = rows.get("trace")\nraw.get("span_id")',
  ]);
  assert.deepEqual(found, ['a.b', 'p.q', 'r.s', 'x.y']);
});

test('the attribute keys looked up are the expected ones, and the page documents them', () => {
  const found = otlpLookupKeys(lookupSources());
  assert.deepEqual(found, EXPECTED_LOOKUPS, 'the code looks up different keys than this test lists');
  assert.deepEqual(missingFrom(mappingPage(), found), []);
});

const finishSource = () => readRepo('backend/app/ingest/finish_reason.py');
const errorSource = () => readRepo('backend/app/ingest/error_class.py');

test('the finish reason table matches _CANONICAL', () => {
  assert.deepEqual(finishReasonDrift(finishSource(), mappingPage()), []);
});

test('the finish reason table fails on a changed mapping or a changed page', () => {
  const page = mappingPage();
  const mutatedSource = finishSource().replace('"max_tokens": "length"', '"max_tokens": "stop"');
  assert.notDeepEqual(finishReasonDrift(mutatedSource, page), []);
  const mutatedPage = page.replace('`length`, `max_tokens`', '`length`');
  assert.notDeepEqual(finishReasonDrift(finishSource(), mutatedPage), []);
  const extraRaw = finishSource().replace('"refusal": "content_filter"', '"refusal": "content_filter",\n    "new_reason": "stop"');
  assert.notDeepEqual(finishReasonDrift(extraRaw, page), []);
});

test('the status table matches _STATUS_CLASSES and the 4xx and 5xx rules', () => {
  assert.deepEqual(statusClassDrift(errorSource(), mappingPage()), []);
});

test('the status table fails on a changed class or a changed page', () => {
  const page = mappingPage();
  const mutatedSource = errorSource().replace('429: ErrorClass.RATE_LIMIT', '429: ErrorClass.TIMEOUT');
  assert.notDeepEqual(statusClassDrift(mutatedSource, page), []);
  const mutatedPage = page.replace('| 401, 403 | `auth` |', '| 401, 403 | `client` |');
  assert.notDeepEqual(statusClassDrift(errorSource(), mutatedPage), []);
});

test('the message phrase table matches _MESSAGE_PATTERNS', () => {
  assert.deepEqual(messagePhraseDrift(errorSource(), mappingPage()), []);
});

test('the message phrase table fails on a changed phrase or class', () => {
  const page = mappingPage();
  const mutatedSource = errorSource().replace('"too many requests"', '"slow down"');
  assert.notDeepEqual(messagePhraseDrift(mutatedSource, page), []);
  const mutatedPage = page.replace('`deadline exceeded`, ', '');
  assert.notDeepEqual(messagePhraseDrift(errorSource(), mutatedPage), []);
  const movedClass = errorSource().replace('(ErrorClass.NETWORK, (', '(ErrorClass.CLIENT, (');
  assert.notDeepEqual(messagePhraseDrift(movedClass, page), []);
});
