/**
 * The detector entries moved from the Doctor page to the detector catalogue. A link or bookmark
 * to an old `/docs/doctor/#<detector heading>` anchor lands on the Doctor page without a
 * matching element, so a small script on that page sends the visitor to the catalogue.
 */

export const CATALOGUE_PATH = '/docs/reference/detectors/';

/**
 * The catalogue address for an old Doctor-page hash, or null when the hash is empty or names
 * something that is not a catalogue heading (anchors that still exist on the Doctor page).
 *
 * @param {string} hash          `location.hash`, with or without the leading "#"
 * @param {string[]} catalogueIds the heading ids of the catalogue page
 * @returns {string | null}
 */
export function catalogueRedirect(hash, catalogueIds) {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  if (raw === '') {
    return null;
  }
  let id;
  try {
    id = decodeURIComponent(raw);
  } catch {
    return null;
  }
  return catalogueIds.includes(id) ? `${CATALOGUE_PATH}#${id}` : null;
}

/** The text a heading shows once its Markdown is rendered (code ticks, emphasis and links). */
function headingText(markdown) {
  return markdown
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[`*]/g, '')
    .replace(/(^|\s)_+|_+(\s|$)/g, '$1$2')
    .trim();
}

/** github-slugger's rules, which Starlight uses for heading ids: lowercase, drop punctuation. */
function slug(text) {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\p{Pc}\s-]/gu, '')
    .replace(/ /g, '-');
}

/**
 * The heading ids Starlight gives a Markdown page: the slug of each heading outside code
 * fences, with "-1", "-2" appended to a repeated one.
 *
 * @param {string} markdown
 * @returns {string[]}
 */
export function catalogueIdsFrom(markdown) {
  const body = markdown.replace(/^---\n[\s\S]*?\n---\n/, '');
  const seen = new Map();
  const ids = [];
  let fence = null;
  for (const line of body.split('\n')) {
    const opening = line.match(/^\s*(`{3,}|~{3,})/);
    if (fence) {
      if (opening && opening[1][0] === fence[0] && opening[1].length >= fence.length) fence = null;
      continue;
    }
    if (opening) {
      fence = opening[1];
      continue;
    }
    const heading = line.match(/^#{1,6}\s+(.+?)\s*#*\s*$/);
    if (!heading) continue;
    const base = slug(headingText(heading[1]));
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    ids.push(count === 0 ? base : `${base}-${count}`);
  }
  return ids;
}
