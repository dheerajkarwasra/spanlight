/**
 * What other features may use from `features/traces`: the trace query, the payload view and the
 * chat-message parsing that sessions and review work mode build on, and the variance band the
 * reconciliation page shares with the span cost card. The router loads the pages
 * and the search schemas from their own modules, so they stay lazy.
 */
export { messageText, parseChatPayload, type ChatMessage } from "./chat-messages";
export { varianceTone, type VarianceTone } from "./cost-explanation";
export { PayloadView } from "./payload-view";
export { useTraceQuery } from "./trace-queries";
