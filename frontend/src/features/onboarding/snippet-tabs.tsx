import { Link } from "@tanstack/react-router";

import { CodeBlock } from "@/components/code-block";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMe } from "@/features/auth";

import { buildSnippets, type SnippetId } from "./snippets";

interface SnippetTabsProps {
  /** The secret created during onboarding, or null to show the placeholder. */
  apiKey: string | null;
  /** The selected tab; owned by the step so it can swap the key card on the gateway tab. */
  value: SnippetId;
  onValueChange: (value: SnippetId) => void;
  orgId: string;
  projectId: string;
}

/**
 * The integration snippets (Figma "Snippets"): a segmented pill of tabs above the dark code
 * cards. Every snippet already contains this instance's host and, once created, the real key.
 * The gateway tab uses a gateway key instead, so it links to where one is created.
 */
export function SnippetTabs({ apiKey, value, onValueChange, orgId, projectId }: SnippetTabsProps) {
  const gatewayMode = useMe().capabilities.gateway_mode;
  const snippets = buildSnippets({ apiKey, host: window.location.origin, gatewayMode });

  return (
    <Tabs
      value={value}
      onValueChange={(next) => {
        const match = snippets.find((snippet) => snippet.id === next);
        if (match) {
          onValueChange(match.id);
        }
      }}
      className="flex min-w-0 flex-col"
    >
      <TabsList aria-label="Integration method" className="self-start">
        {snippets.map((snippet) => (
          <TabsTrigger key={snippet.id} value={snippet.id}>
            {snippet.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {snippets.map((snippet) => (
        <TabsContent key={snippet.id} value={snippet.id} className="flex flex-col gap-3 pt-3">
          {snippet.blocks.map((block) => (
            <CodeBlock
              key={block.title}
              code={block.code}
              title={block.filename}
              language={block.language}
              label={`${snippet.label}: ${block.title}`}
            />
          ))}
          <p className="text-xs font-medium text-muted-foreground">
            {snippet.description}
            {snippet.id === "gateway" ? (
              <>
                {" "}
                <Link
                  to="/$orgId/$projectId/gateway/credentials"
                  params={{ orgId, projectId }}
                  className="font-semibold text-accent underline-offset-2 hover:underline"
                >
                  Gateway › Credentials →
                </Link>
              </>
            ) : null}
          </p>
        </TabsContent>
      ))}
    </Tabs>
  );
}
