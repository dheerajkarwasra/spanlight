import { Fragment, useState } from "react";

import { CopyButton } from "@/components/copy-button";
import { StatusDot } from "@/components/status-dot";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { buildSnippets, DEFAULT_ENVIRONMENT, type Snippet } from "@/features/onboarding/snippets";
import type { CodeTokenKind } from "@/lib/highlight";

import { snippetLines } from "./snippet-lines";

/** The snippet tabs shown here; plain curl stays in the full onboarding flow. */
const SNIPPET_TABS = ["python", "openai", "anthropic", "otel"] as const;

const TOKEN_CLASSES: Record<CodeTokenKind, string | undefined> = {
  plain: undefined,
  keyword: "text-rail-muted-foreground",
  decorator: "text-code-string",
  string: "text-code-string",
  comment: "text-code-comment",
  prompt: "text-code-comment select-none",
};

export function SnippetPanel({ environment }: { environment: string | undefined }) {
  const snippets = buildSnippets({
    apiKey: null,
    host: window.location.origin,
    environment: environment ?? DEFAULT_ENVIRONMENT,
  }).filter((snippet) => (SNIPPET_TABS as readonly string[]).includes(snippet.id));
  const [active, setActive] = useState<string>(snippets[0]?.id ?? "python");
  const activeSnippet = snippets.find((snippet) => snippet.id === active) ?? snippets[0];
  const copyValue = activeSnippet?.blocks.at(-1)?.code ?? "";

  return (
    <Tabs
      value={active}
      onValueChange={setActive}
      className="relative flex min-w-0 flex-1 flex-col overflow-hidden rounded-[22px] bg-hero-card text-hero-card-foreground shadow-lg xl:self-start dark:border dark:border-border"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rail-tile p-3">
        <TabsList aria-label="Integration method" className="border-0 bg-rail-tile p-[3px]">
          {snippets.map((snippet) => (
            <TabsTrigger
              key={snippet.id}
              value={snippet.id}
              className="h-auto px-3 py-1.5 text-xs text-rail-muted-foreground hover:text-rail-foreground focus-visible:outline-lime data-[state=active]:bg-lime data-[state=active]:font-medium data-[state=active]:text-lime-foreground data-[state=active]:shadow-none dark:data-[state=active]:bg-lime"
            >
              {snippet.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <CopyButton
          value={copyValue}
          label="Copy"
          aria-label={`Copy the ${activeSnippet?.label ?? ""} code`}
          tone="ink"
          showLabel
          size="sm"
          className="h-auto shrink-0 border border-rail-tile bg-transparent py-1.5 pr-3 pl-2.5 text-xs font-medium text-rail-muted-foreground [&_svg]:size-3.5"
        />
      </div>
      {snippets.map((snippet) => (
        <TabsContent key={snippet.id} value={snippet.id} className="pt-0">
          <SnippetListing snippet={snippet} />
        </TabsContent>
      ))}
      <div className="mt-auto flex items-center gap-2.5 border-t border-rail-tile px-5 py-3.5">
        <StatusDot state="live" />
        <p className="text-xs font-medium text-rail-muted-foreground">
          Waiting for the first span{environment ? ` from ${environment}` : ""}…
        </p>
      </div>
    </Tabs>
  );
}

function SnippetListing({ snippet }: { snippet: Snippet }) {
  const lines = snippetLines(snippet.blocks);
  return (
    <pre
      tabIndex={0}
      aria-label={`${snippet.label} setup code`}
      className="max-h-[348px] overflow-auto px-5 py-[18px] font-mono text-code text-rail-foreground focus-visible:outline-offset-[-2px] focus-visible:outline-lime"
    >
      <code className="flex w-max min-w-full flex-col">
        {lines.map((tokens, lineIndex) => (
          <span key={lineIndex} className="flex gap-4">
            <span
              aria-hidden
              className="w-[18px] shrink-0 text-right text-rail-subtle-foreground select-none"
            >
              {lineIndex + 1}
            </span>
            <span className="whitespace-pre">
              {tokens.map((token, tokenIndex) => {
                const tokenClass = TOKEN_CLASSES[token.kind];
                return tokenClass ? (
                  <span key={tokenIndex} className={tokenClass}>
                    {token.text}
                  </span>
                ) : (
                  <Fragment key={tokenIndex}>{token.text}</Fragment>
                );
              })}
            </span>
          </span>
        ))}
      </code>
    </pre>
  );
}
