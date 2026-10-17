import { AlertCircle } from "lucide-react";
import { Select as SelectPrimitive } from "radix-ui";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";

/** The keys the menu offers and the state of the request for the seen ones. */
export interface KeyOptions {
  promoted: readonly string[];
  /** Keys seen on traces in the last 7 days; `undefined` while loading or after a failure. */
  seen: readonly string[] | undefined;
  failed: boolean;
  onRetry: () => void;
}

interface MetadataKeySelectProps {
  value: string | undefined;
  options: KeyOptions;
  onChange: (key: string) => void;
}

const GROUP_LABEL = "px-3 pt-2 pb-1.5 text-overline text-muted-foreground uppercase";

/** Promoted keys first, then the other keys seen in 7 days; the URL's key is always listed. */
function menuKeys(value: string | undefined, { promoted, seen = [] }: KeyOptions) {
  const others = [...new Set([...seen, ...(value ? [value] : [])])]
    .filter((key) => !promoted.includes(key))
    .sort();
  return { promoted, others };
}

/** Figma "Attribution / Picker": the key menu shown for Metadata only. */
export function MetadataKeySelect({ value, options, onChange }: MetadataKeySelectProps) {
  const { promoted, others } = menuKeys(value, options);
  const empty = promoted.length === 0 && others.length === 0;
  return (
    <div className="flex flex-col gap-1.5">
      <Select value={value ?? ""} onValueChange={onChange}>
        <SelectTrigger
          aria-label="Metadata key"
          className="relative h-10 w-full rounded-full px-4 before:absolute before:inset-x-0 before:-inset-y-0.5 md:w-auto md:min-w-56 md:before:hidden"
        >
          <span className="flex min-w-0 items-center gap-2">
            <span className="text-xs text-muted-foreground">Key</span>
            {value ? (
              <span className="truncate font-mono text-code text-foreground">{value}</span>
            ) : (
              <span className="text-subtle-foreground">
                {options.failed ? "Couldn’t load the keys" : "Pick a key"}
              </span>
            )}
            {value && promoted.includes(value) ? (
              <Badge variant="accent" size="sm">
                promoted
              </Badge>
            ) : null}
          </span>
        </SelectTrigger>
        <SelectContent className="w-72">
          {empty ? (
            <div className="flex flex-col gap-1 px-3 py-2 text-sm">
              <p className="font-semibold text-foreground">No metadata keys yet</p>
              <p className="text-muted-foreground">
                Set <code className="font-mono">metadata</code> on a trace and its keys show up here
                within a minute.
              </p>
            </div>
          ) : null}
          {promoted.length > 0 ? (
            <SelectGroup>
              <SelectPrimitive.Label className={GROUP_LABEL}>Promoted</SelectPrimitive.Label>
              {promoted.map((key) => (
                <SelectItem key={key} value={key} className="font-mono">
                  {key}
                </SelectItem>
              ))}
            </SelectGroup>
          ) : null}
          {promoted.length > 0 && others.length > 0 ? <Separator className="my-1" /> : null}
          {others.length > 0 ? (
            <SelectGroup>
              <SelectPrimitive.Label className={GROUP_LABEL}>
                Seen in the last 7 days
              </SelectPrimitive.Label>
              {others.map((key) => (
                <SelectItem key={key} value={key} className="font-mono">
                  {key}
                </SelectItem>
              ))}
            </SelectGroup>
          ) : null}
          <p className="px-3 pt-2 pb-1 text-xs text-muted-foreground">
            Windows over 24 hours need a promoted key.
          </p>
        </SelectContent>
      </Select>
      {options.failed ? (
        <p role="alert" className="flex items-center gap-1.5 text-xs text-danger-text">
          <AlertCircle aria-hidden className="size-3.5" />
          Request failed.
          <Button variant="link" size="sm" onClick={options.onRetry} className="text-xs">
            Try again
          </Button>
        </p>
      ) : null}
    </div>
  );
}
