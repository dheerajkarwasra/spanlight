/**
 * What other features may use from `features/traces`: the trace query, the payload view and the
 * chat-message parsing that sessions and review work mode build on, the text diff a session's
 * system prompt reuses, the variance band the reconciliation page shares with the span cost card,
 * and the bulk-selection row checkbox. The waterfall has its own entry (`./waterfall`), so pages
 * that import this barrel don't download it. The router loads the pages and the search schemas
 * from their own modules, so they stay lazy.
 */
export { messageText, parseChatPayload, type ChatMessage } from "./chat-messages";
export { stepDiff } from "./compare-diff";
export { DiffColumn } from "./compare-diff-lines";
export { varianceTone, type VarianceTone } from "./cost-explanation";
export { PayloadView } from "./payload-view";
export { RowCheckbox } from "./row-checkbox";
export { useSpansWithPayloads, useTraceQuery } from "./trace-queries";
