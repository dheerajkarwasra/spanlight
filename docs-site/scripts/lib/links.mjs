// Rewrites the relative links of a repository Markdown file so they work on the docs site.
//
// A link to a page the site publishes becomes a site path, a link to another file in the
// repository becomes a GitHub URL, and a link to a file that is private to the maintainers or
// to a local machine is replaced by its plain text. A link to a file that does not exist is an
// error: the same link would be dead on GitHub. (The Docker build has only the published
// documents, not the whole repository, so it skips that existence check; CI runs it.)

import fs from 'node:fs';
import path from 'node:path';

const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

// Paths (relative to the repository root) that are never linked from the public site.
const PRIVATE_PREFIXES = [
  'HANDOFF.md',
  'CLAUDE.md',
  '.claude',
  '.superpowers',
  '.local',
  'docs/plans',
  'docs/specs',
  'docs/design',
  'docs/ROADMAP.md',
  'docs/architecture.md',
];

export const isPrivate = (repoPath) =>
  PRIVATE_PREFIXES.some((p) => repoPath === p || repoPath.startsWith(`${p}/`)) ||
  /(^|\/)\.env(\.[^/]+)?$/.test(repoPath);

/**
 * @param {string} markdown      body of the source file
 * @param {string} source        repository-relative path of the source file
 * @param {{repoRoot: string, githubBlob: string, githubTree: string, published: Map<string,string>, checkTargets: boolean}} ctx
 * @param {string[]} errors      collects unresolvable links
 */
export function rewriteLinks(markdown, source, ctx, errors) {
  const lines = markdown.split('\n');
  let fence = null;
  const out = lines.map((line, index) => {
    const opening = line.match(/^\s*(`{3,}|~{3,})/);
    if (fence) {
      if (opening && opening[1][0] === fence[0] && opening[1].length >= fence.length) fence = null;
      return line;
    }
    if (opening) {
      fence = opening[1];
      return line;
    }
    return rewriteLine(line, source, index + 1, ctx, errors);
  });
  return out.join('\n');
}

function rewriteLine(line, source, lineNumber, ctx, errors) {
  // Mask code spans so a link-looking string inside one is left alone and a code span inside a
  // link's text does not break the match.
  const spans = [];
  const masked = line.replace(/(`+)[^`]*?\1/g, (match) => {
    spans.push(match);
    return `\u0000${spans.length - 1}\u0000`;
  });

  const resolve = (target) => resolveTarget(target, source, lineNumber, ctx, errors);

  let result = masked.replace(
    /(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*(<[^>]*>|[^\s)]*)((?:\s+"[^"]*")?)\s*\)/g,
    (match, bang, text, target, title) => {
      const bare = target.startsWith('<') ? target.slice(1, -1) : target;
      const resolved = resolve(bare);
      if (resolved === undefined) return match;
      if (resolved === null) return bang ? '' : text; // private or local-only: keep the words
      return `${bang}[${text}](${resolved}${title})`;
    },
  );
  result = result.replace(/^(\s*\[[^\]]+\]:\s*)(\S+)/, (match, head, target) => {
    const resolved = resolve(target);
    return resolved === undefined || resolved === null ? match : `${head}${resolved}`;
  });
  return result.replace(/\u0000(\d+)\u0000/g, (_, i) => spans[Number(i)]);
}

/** undefined: leave as is. null: drop the link. string: the new target. */
function resolveTarget(target, source, lineNumber, ctx, errors) {
  if (!target || target.startsWith('#') || target.startsWith('//') || SCHEME.test(target)) {
    return undefined;
  }
  const hashAt = target.search(/[#?]/);
  const filePart = decodeURI(hashAt === -1 ? target : target.slice(0, hashAt));
  const suffix = hashAt === -1 ? '' : target.slice(hashAt);
  const fragment = suffix.startsWith('#') ? suffix : '';

  const repoPath = path.posix.normalize(path.posix.join(path.posix.dirname(source), filePart));
  if (repoPath.startsWith('..')) return null;
  const clean = repoPath.replace(/\/$/, '') || '.';
  if (clean === '.') return `${ctx.githubTree}${fragment}`;

  const published = ctx.published.get(clean);
  if (published) return `${published}${fragment}`;
  if (isPrivate(clean)) return null;

  const absolute = path.join(ctx.repoRoot, clean);
  let directory = filePart.endsWith('/');
  if (ctx.checkTargets) {
    if (!fs.existsSync(absolute)) {
      errors.push(`${source}:${lineNumber}: link target does not exist: ${target}`);
      return null;
    }
    directory = fs.statSync(absolute).isDirectory();
  }
  const base = directory ? ctx.githubTree : ctx.githubBlob;
  return `${base}/${clean}${fragment}`;
}
