import { CopyButton } from "@/components/copy-button";
import { JsonViewer } from "@/components/json-viewer";
import type { Span } from "@/lib/api";
import { formatDuration, formatInteger, formatTimestamp } from "@/lib/format";

import { payloadToClipboardText } from "./chat-messages";
import { MetaField } from "./meta-field";
import { PromptVersionRef } from "./prompt-version-ref";
import { metadataAttributes } from "./provider-error-format";

/**
 * Figma "Panel / Metadata": timing and IDs, then the custom attributes as JSON. The keys the
 * provider error block above renders are left out of the list while it shows; the Raw JSON tab
 * keeps every key.
 */
export function SpanMetadata({ span }: { span: Span }) {
  const attributes = metadataAttributes(span);
  const attributeCount = Object.keys(attributes).length;
  const hiddenCount = Object.keys(span.attributes).length - attributeCount;

  return (
    <div className="flex flex-col gap-5">
      <dl className="flex flex-col">
        <MetaField layout="inline" label="Started">
          <span className="tabular">{formatTimestamp(span.started_at)}</span>
        </MetaField>
        <MetaField layout="inline" label="Ended">
          <span className="tabular">{formatTimestamp(span.ended_at)}</span>
        </MetaField>
        <MetaField layout="inline" label="Duration">
          <span className="tabular">{formatDuration(span.duration_ms)}</span>
        </MetaField>
        <MetaField
          layout="inline"
          label="Span ID"
          mono
          copyValue={span.span_id}
          copyLabel="Copy span ID"
        >
          {span.span_id}
        </MetaField>
        {span.parent_span_id ? (
          <MetaField
            layout="inline"
            label="Parent span ID"
            mono
            copyValue={span.parent_span_id}
            copyLabel="Copy parent span ID"
          >
            {span.parent_span_id}
          </MetaField>
        ) : (
          <MetaField layout="inline" label="Parent span ID">
            <span className="text-muted-foreground">None (root span)</span>
          </MetaField>
        )}
        {span.prompt_version_id ? (
          <MetaField
            layout="inline"
            label="Prompt version"
            copyValue={span.prompt_version_id}
            copyLabel="Copy prompt version ID"
          >
            <PromptVersionRef versionId={span.prompt_version_id} />
          </MetaField>
        ) : null}
      </dl>

      <section aria-label="Attributes" className="flex flex-col gap-2">
        <div className="flex h-7 items-center gap-1.5">
          <h3 className="text-sm font-medium text-foreground">Attributes</h3>
          <span className="text-xs font-medium text-subtle-foreground tabular">
            {formatInteger(attributeCount)}
          </span>
          {attributeCount > 0 ? (
            <CopyButton
              value={payloadToClipboardText(attributes)}
              label="Copy attributes"
              className="ml-auto size-7"
            />
          ) : null}
        </div>
        {hiddenCount > 0 ? (
          <p className="text-xs font-medium text-muted-foreground">
            Keys shown in the block above are left out here; Raw JSON has every key.
          </p>
        ) : null}
        {attributeCount === 0 ? (
          <p className="text-sm text-muted-foreground">No custom attributes on this span.</p>
        ) : (
          <JsonViewer
            value={attributes}
            defaultExpandDepth={3}
            className="rounded-input px-3 py-3.5"
          />
        )}
      </section>
    </div>
  );
}
