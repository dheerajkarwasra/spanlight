import { useState } from "react";

import { Callout } from "@/components/callout";
import { CodeBlock } from "@/components/code-block";
import { DialogHeading } from "@/components/dialog-heading";
import { SecretBox } from "@/components/secret-box";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogFooter } from "@/components/ui/dialog";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { useMe } from "@/features/auth";
import { formatInteger } from "@/lib/format";
import type { CreatedGatewayKey } from "@/lib/api";
import { REVEAL_COPY } from "@/lib/reveal-copy";

import { EnvironmentBadge } from "../environment-badge";
import { GATEWAY_MODE_NOTES } from "../gateway-url";
import { cacheLabel } from "./key-form";
import { sdkSnippet, type SdkChoice } from "./key-snippets";

interface KeySecretRevealProps {
  created: CreatedGatewayKey;
  /** The route's name, to show in the summary line. */
  routeName: string | null;
}

/**
 * The second step of the create dialog (Figma "Key reveal"). It shares the Settings reveal
 * layout: the warning, the secret in an ink box with a Copy button, then what the key does and
 * how to point an SDK at it. The caller keeps the secret in state only.
 */
export function KeySecretReveal({ created, routeName }: KeySecretRevealProps) {
  const copy = REVEAL_COPY.gateway_key;
  const [sdk, setSdk] = useState<SdkChoice>("openai");
  const mode = useMe().capabilities.gateway_mode;
  const rpm = formatInteger(created.rpm_limit);
  const tpm = formatInteger(created.tpm_limit);

  return (
    <div className="grid gap-[18px]">
      <DialogHeading
        title={copy.title}
        description={
          <>
            <span className="font-semibold text-foreground">{created.name}</span> is ready to use.
          </>
        }
      />

      <Callout tone="warning">{copy.warning}</Callout>

      <SecretBox
        label={copy.secretLabel}
        secret={created.secret}
        copyAriaLabel={copy.copyAriaLabel}
      />

      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs font-medium text-muted-foreground">
        <EnvironmentBadge environment={created.environment} />
        <span aria-hidden className="text-subtle-foreground">
          ·
        </span>
        <span>Route {routeName ?? "not set"}</span>
        <span aria-hidden className="text-subtle-foreground">
          ·
        </span>
        <span>{rpm ? `${rpm} rpm` : "No rpm limit"}</span>
        <span aria-hidden className="text-subtle-foreground">
          ·
        </span>
        <span>{tpm ? `${tpm} tpm` : "No tpm limit"}</span>
        <span aria-hidden className="text-subtle-foreground">
          ·
        </span>
        <span>cache {cacheLabel(created.cache_ttl_seconds).toLowerCase()}</span>
      </p>

      <div className="grid gap-2.5">
        <p className="text-xs font-medium text-muted-foreground">
          Point your SDK at the gateway with this key.
          {mode === "embedded" ? null : ` ${GATEWAY_MODE_NOTES[mode]}`}
        </p>
        <SegmentedControl
          aria-label="SDK"
          tone="surface"
          value={sdk}
          onValueChange={setSdk}
          options={[
            { value: "openai", label: "OpenAI SDK" },
            { value: "anthropic", label: "Anthropic SDK" },
          ]}
          className="self-start"
        />
        <CodeBlock
          code={sdkSnippet(sdk, mode)}
          label={sdk === "openai" ? "OpenAI SDK setup" : "Anthropic SDK setup"}
          title={sdk === "openai" ? "openai_client.py" : "anthropic_client.py"}
          language="python"
        />
      </div>

      <DialogFooter>
        <DialogClose asChild>
          <Button variant="primary">Done</Button>
        </DialogClose>
      </DialogFooter>
    </div>
  );
}
