/** Iterative depth-first traversal: one frame per depth, even for very wide forests. */
import type { SpanNode } from "./span-tree";

export function* walkSpanTree(roots: readonly SpanNode[], collapsed?: ReadonlySet<string>) {
  const stack = [{ siblings: roots, index: 0 }];
  while (stack.length > 0) {
    const frame = stack.at(-1);
    if (!frame) break;
    const node = frame.siblings[frame.index];
    if (!node) {
      stack.pop();
      continue;
    }
    const index = frame.index++;
    yield { node, depth: stack.length - 1, posInSet: index + 1, setSize: frame.siblings.length };
    if (node.children.length > 0 && !collapsed?.has(node.span.span_id)) {
      stack.push({ siblings: node.children, index: 0 });
    }
  }
}
