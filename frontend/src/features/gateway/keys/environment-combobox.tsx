import { Check, ChevronDown } from "lucide-react";
import { useState } from "react";

import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import { ENVIRONMENT_PRESETS } from "../environment";
import { ENVIRONMENT_MAX_LENGTH } from "./key-form";

interface EnvironmentComboboxProps {
  value: string;
  onChange: (value: string) => void;
  /** Wired by `FormField`: the label's target and the hint/error description. */
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

/**
 * Figma "Gateway/Combobox field": the four presets, or any text of your own (`Use "qa-eu"`).
 * The API stores free text, so a custom environment is a normal value, shown as "other" in tags.
 */
export function EnvironmentCombobox({
  value,
  onChange,
  id,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: EnvironmentComboboxProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const typed = search.trim();
  const query = typed.toLowerCase();

  const presets = ENVIRONMENT_PRESETS.filter((preset) => preset.includes(query));
  const offerCustom = typed !== "" && !ENVIRONMENT_PRESETS.some((preset) => preset === query);

  function choose(next: string) {
    onChange(next);
    setOpen(false);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setSearch("");
        }
      }}
    >
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-invalid={ariaInvalid}
          aria-describedby={ariaDescribedBy}
          className={cn(
            "flex h-[46px] w-full items-center justify-between gap-2 rounded-input border border-input bg-surface px-4 text-sm text-foreground",
            "field-focus transition-colors duration-200 ease-out-quart",
            "aria-invalid:border-[1.5px] aria-invalid:border-danger",
          )}
        >
          <span className={cn("truncate", value === "" && "text-subtle-foreground")}>
            {value === "" ? "Choose an environment" : value}
          </span>
          <ChevronDown aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        sideOffset={4}
        className="w-(--radix-popover-trigger-width) min-w-60 overflow-hidden p-0"
      >
        <Command shouldFilter={false} loop>
          <CommandInput
            value={search}
            onValueChange={setSearch}
            maxLength={ENVIRONMENT_MAX_LENGTH}
            placeholder="Search or type your own"
            aria-label="Environment"
            className="h-11"
          />
          <CommandList className="max-h-64 p-1.5">
            <CommandGroup className="p-0">
              {presets.map((preset) => (
                <CommandItem
                  key={preset}
                  value={preset}
                  onSelect={() => {
                    choose(preset);
                  }}
                  className="h-auto min-h-10 py-1.5"
                >
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-medium text-foreground">{preset}</span>
                    {preset === "production" ? (
                      <span className="text-xs text-muted-foreground">
                        Lab faults never run here
                      </span>
                    ) : null}
                  </span>
                  {value === preset ? <Check aria-hidden /> : null}
                </CommandItem>
              ))}
              {offerCustom ? (
                <CommandItem
                  value={`custom:${typed}`}
                  onSelect={() => {
                    choose(typed);
                  }}
                  className="h-auto min-h-10 py-1.5"
                >
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-medium text-foreground">Use “{typed}”</span>
                    <span className="text-xs text-muted-foreground">
                      Custom environment · shown as other
                    </span>
                  </span>
                </CommandItem>
              ) : null}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
