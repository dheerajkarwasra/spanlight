import { Key, Lock, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { CopyButton } from "@/components/copy-button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import type { CreatedApiKey, Role } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/permissions";
import { cn } from "@/lib/utils";

import { INK_FOCUS, KEY_FIELD_CLASSES } from "./api-key-styles";
import { ONBOARDING_COPY } from "./copy";
import { CreateKeyForm } from "./create-key-form";

interface ApiKeyCardProps {
  projectId: string;
  projectName: string;
  canCreateKeys: boolean;
  role: Role;
  createdKey: CreatedApiKey | null;
  onCreated: (key: CreatedApiKey) => void;
  /** Shown under the secret: the "Send a test trace" button. */
  testTrace: ReactNode;
}

/**
 * The ink "API key" card (Figma "API key card"). Creates a key for the project, then shows the
 * secret once with a copy button. The secret lives in the parent's state, never in a cache.
 */
export function ApiKeyCard({
  projectId,
  projectName,
  canCreateKeys,
  role,
  createdKey,
  onCreated,
  testTrace,
}: ApiKeyCardProps) {
  let body: ReactNode;
  if (createdKey) {
    body = <SecretReveal apiKey={createdKey} testTrace={testTrace} />;
  } else if (canCreateKeys) {
    body = <CreateKeyForm projectId={projectId} onCreated={onCreated} />;
  } else {
    body = (
      <p className="flex items-start gap-2 text-sm text-rail-muted-foreground">
        <Lock aria-hidden className="mt-0.5 size-4 shrink-0" />
        <span>
          Your role ({ROLE_LABELS[role]}) can't create API keys. Ask an admin or member for a key,
          then use it in place of <code className="text-rail-foreground">&lt;YOUR_API_KEY&gt;</code>{" "}
          below.
        </span>
      </p>
    );
  }

  const subtitle = createdKey ? `${createdKey.name} · ${projectName}` : projectName;

  return (
    <Card variant="hero" className="flex flex-col gap-3.5 overflow-hidden p-5 sm:p-6">
      <div className="relative flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            aria-hidden
            className="flex size-8 shrink-0 items-center justify-center rounded-[10px] bg-lime text-lime-foreground"
          >
            <Key className="size-4" strokeWidth={2} />
          </span>
          <h2 className="text-sm font-semibold whitespace-nowrap text-rail-foreground">
            {ONBOARDING_COPY.connect.cardTitle}
          </h2>
          <p className="min-w-0 truncate text-xs font-medium text-rail-muted-foreground">
            {subtitle}
          </p>
        </div>
        {createdKey ? <Badge variant="lime">Created just now</Badge> : null}
      </div>

      <div className="relative">{body}</div>
    </Card>
  );
}

/** The one and only time the secret is shown. */
function SecretReveal({ apiKey, testTrace }: { apiKey: CreatedApiKey; testTrace: ReactNode }) {
  return (
    <div className="flex flex-col gap-3.5">
      <div className={KEY_FIELD_CLASSES}>
        <code className="min-w-0 flex-1 px-3 py-1 text-code break-all text-rail-foreground sm:px-0">
          {apiKey.secret}
        </code>
        <CopyButton
          value={apiKey.secret}
          label="Copy"
          aria-label="Copy API key"
          showLabel
          variant="highlight"
          size="md"
          className={cn("w-full sm:w-auto [&_svg]:text-lime-foreground", INK_FOCUS)}
        />
      </div>
      {testTrace}
      <p className="flex items-start gap-2 text-sm text-rail-muted-foreground">
        <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-lime" strokeWidth={2} />
        {ONBOARDING_COPY.connect.cardDescription}
      </p>
    </div>
  );
}
