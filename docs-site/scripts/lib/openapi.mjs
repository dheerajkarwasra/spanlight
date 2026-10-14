// Renders backend/openapi.json as Markdown: one page per tag, a schemas page and an overview.
// Pure functions: the caller reads the document and writes the files.

const SITE_BASE = '/docs';
const HTTP_METHODS = ['get', 'post', 'put', 'patch', 'delete'];
const SCHEMAS_PAGE = `${SITE_BASE}/api/schemas/`;

/** The anchor Starlight (github-slugger) gives a heading with this text. */
export function slug(text) {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\p{M}_\- ]/gu, '')
    .replace(/ /g, '-');
}

const cell = (text) => String(text ?? '').replace(/\|/g, '\\|').replace(/\s*\n\s*/g, ' ').replace(/</g, '&lt;');
const code = (text) => `\`${String(text).replace(/`/g, "'")}\``;

function dedent(text) {
  const lines = text.replace(/\t/g, '    ').split('\n');
  const indents = lines.slice(1).filter((l) => l.trim()).map((l) => l.match(/^ */)[0].length);
  const strip = indents.length ? Math.min(...indents) : 0;
  return [lines[0].trim(), ...lines.slice(1).map((l) => l.slice(strip))].join('\n').trim();
}

/** Free text from a docstring: safe to embed as paragraphs. */
const prose = (text) => dedent(text ?? '').replace(/</g, '&lt;');

function refName(ref) {
  return ref.split('/').pop();
}

function typeOf(schema) {
  if (!schema || Object.keys(schema).length === 0) return 'any';
  if (schema.$ref) {
    const name = refName(schema.$ref);
    return `[${name}](${SCHEMAS_PAGE}#${slug(name)})`;
  }
  const variants = schema.anyOf ?? schema.oneOf;
  if (variants) {
    const parts = variants.map(typeOf);
    return [...new Set(parts)].join(' or ');
  }
  if (schema.allOf) return schema.allOf.map(typeOf).join(' and ');
  if (schema.enum) return schema.enum.map((v) => code(JSON.stringify(v))).join(' \\| ');
  if (schema.const !== undefined) return code(JSON.stringify(schema.const));
  if (schema.type === 'array') return `array of ${typeOf(schema.items)}`;
  if (schema.type === 'object') {
    if (schema.additionalProperties && typeof schema.additionalProperties === 'object') {
      return `object of ${typeOf(schema.additionalProperties)}`;
    }
    return 'object';
  }
  if (schema.type === 'null') return 'null';
  const base = Array.isArray(schema.type) ? schema.type.join(' or ') : (schema.type ?? 'any');
  return schema.format ? `${base} (${schema.format})` : base;
}

function constraints(schema) {
  const out = [];
  const pick = (s) => {
    for (const [key, label] of [
      ['minLength', 'min length'],
      ['maxLength', 'max length'],
      ['minimum', 'min'],
      ['maximum', 'max'],
      ['minItems', 'min items'],
      ['maxItems', 'max items'],
      ['pattern', 'pattern'],
    ]) {
      if (s[key] !== undefined) out.push(`${label} ${code(s[key])}`);
    }
  };
  pick(schema);
  for (const variant of schema.anyOf ?? []) pick(variant);
  return out;
}

function describeProperty(schema) {
  const parts = [];
  if (schema.description) parts.push(cell(schema.description));
  const limits = constraints(schema);
  if (limits.length) parts.push(limits.join(', '));
  if ('default' in schema && schema.default !== null) {
    parts.push(`Default ${code(JSON.stringify(schema.default))}.`);
  }
  return parts.join(' ');
}

function propertiesTable(schema) {
  const required = new Set(schema.required ?? []);
  const rows = Object.entries(schema.properties ?? {}).map(
    ([name, property]) =>
      `| ${code(name)} | ${typeOf(property)} | ${required.has(name) ? 'yes' : 'no'} | ${describeProperty(property)} |`,
  );
  if (rows.length === 0) return '';
  return ['| Field | Type | Required | Notes |', '| --- | --- | --- | --- |', ...rows].join('\n');
}

function operations(document) {
  const found = [];
  for (const [path, item] of Object.entries(document.paths ?? {})) {
    for (const method of HTTP_METHODS) {
      const operation = item[method];
      if (!operation) continue;
      found.push({
        method: method.toUpperCase(),
        path,
        operation,
        parameters: [...(item.parameters ?? []), ...(operation.parameters ?? [])],
        tag: operation.tags?.[0] ?? 'other',
      });
    }
  }
  return found;
}

const heading = (op) => `${op.method} ${op.path}`;

function renderOperation(op) {
  const out = [`## ${code(heading(op))}`, ''];
  if (op.operation.summary) out.push(`**${cell(op.operation.summary)}**`, '');
  if (op.operation.description) out.push(prose(op.operation.description), '');
  if (op.operation.deprecated) out.push(':::caution', 'This operation is deprecated.', ':::', '');

  if (op.parameters.length) {
    out.push('| Parameter | In | Type | Required | Notes |', '| --- | --- | --- | --- | --- |');
    for (const p of op.parameters) {
      out.push(
        `| ${code(p.name)} | ${p.in} | ${typeOf(p.schema)} | ${p.required ? 'yes' : 'no'} | ${describeProperty({ ...p.schema, description: p.description ?? p.schema?.description })} |`,
      );
    }
    out.push('');
  }

  const body = op.operation.requestBody;
  if (body) {
    const [[mediaType, media]] = Object.entries(body.content ?? { 'application/json': {} });
    out.push(
      `**Request body** (${code(mediaType)}${body.required ? ', required' : ''}): ${typeOf(media.schema)}`,
      '',
    );
  }

  const responses = Object.entries(op.operation.responses ?? {});
  if (responses.length) {
    out.push('| Status | Description | Body |', '| --- | --- | --- |');
    for (const [status, response] of responses) {
      const content = Object.entries(response.content ?? {});
      const bodyText = content.length
        ? content.map(([type, media]) => `${code(type)}: ${typeOf(media.schema)}`).join('<br>')
        : 'none';
      out.push(`| ${status} | ${cell(response.description)} | ${bodyText} |`);
    }
    out.push('');
  }
  return out.join('\n');
}

const titleCase = (tag) => tag.charAt(0).toUpperCase() + tag.slice(1);

/** Returns [{ file, content }] relative to the docs content directory. */
export function renderOpenApi(document) {
  const all = operations(document);
  const tags = [...new Set(all.map((o) => o.tag))].sort();
  const files = [];
  const version = document.info?.version ?? '';

  tags.forEach((tag, index) => {
    const ops = all
      .filter((o) => o.tag === tag)
      .sort((a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method));
    const body = ops.map(renderOperation).join('\n');
    files.push({
      file: `api/${slug(tag)}.md`,
      content: frontmatter({
        title: `${titleCase(tag)} endpoints`,
        description: `Reference for the ${tag} endpoints of the Spanlight HTTP API.`,
        order: 10 + index,
      }) + `${body}\n`,
    });
  });

  const overview = [
    `This reference is generated from the committed OpenAPI document (version ${version}). The same document is served by every instance at \`/api/openapi.json\`, with an interactive explorer at \`/api/docs\`, and a copy is published here: [openapi.json](${SITE_BASE}/openapi.json).`,
    '',
    'Dashboard routes live under `/api/v1`. Telemetry ingestion (`/v1/traces`, `/v1/otlp/traces`) is versioned on its own. Errors, authentication, rate limits and idempotency keys are described in [API conventions](' +
      `${SITE_BASE}/api/conventions/).`,
    '',
  ];
  for (const tag of tags) {
    overview.push(`## [${titleCase(tag)}](${SITE_BASE}/api/${slug(tag)}/)`, '');
    overview.push('| Operation | Summary |', '| --- | --- |');
    for (const op of all.filter((o) => o.tag === tag).sort((a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method))) {
      overview.push(
        `| [${code(heading(op))}](${SITE_BASE}/api/${slug(tag)}/#${slug(heading(op))}) | ${cell(op.operation.summary)} |`,
      );
    }
    overview.push('');
  }
  files.push({
    file: 'api/index.md',
    content:
      frontmatter({
        title: 'API reference',
        description: 'Every HTTP endpoint of the Spanlight API, generated from the OpenAPI document.',
        order: 0,
        label: 'Overview',
      }) + overview.join('\n'),
  });

  const schemas = document.components?.schemas ?? {};
  const schemaBody = [
    `Request and response bodies used by the [endpoint reference](${SITE_BASE}/api/). Generated from the OpenAPI document.`,
    '',
  ];
  for (const name of Object.keys(schemas).sort()) {
    const schema = schemas[name];
    schemaBody.push(`## ${name}`, '');
    if (schema.description) schemaBody.push(prose(schema.description), '');
    if (schema.enum) {
      schemaBody.push(`One of ${typeOf(schema)}.`, '');
    } else if (schema.properties) {
      schemaBody.push(propertiesTable(schema), '');
    } else {
      schemaBody.push(`Type: ${typeOf(schema)}`, '');
    }
  }
  files.push({
    file: 'api/schemas.md',
    content:
      frontmatter({
        title: 'API schemas',
        description: 'The request and response objects of the Spanlight HTTP API.',
        order: 99,
      }) + schemaBody.join('\n'),
  });
  return files;
}

export function frontmatter({ title, description, order, label, editUrl = false }) {
  const lines = ['---', `title: ${JSON.stringify(title)}`];
  if (description) lines.push(`description: ${JSON.stringify(description)}`);
  // Generated pages have no file under docs-site/: link their real source, or show no edit link.
  lines.push(`editUrl: ${editUrl === false ? 'false' : JSON.stringify(editUrl)}`);
  if (order !== undefined || label) {
    lines.push('sidebar:');
    if (order !== undefined) lines.push(`  order: ${order}`);
    if (label) lines.push(`  label: ${JSON.stringify(label)}`);
  }
  lines.push('---', '', '');
  return lines.join('\n');
}
