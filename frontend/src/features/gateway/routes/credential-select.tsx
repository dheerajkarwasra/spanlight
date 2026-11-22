import { Controller, useFormContext } from "react-hook-form";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Credential } from "@/lib/api";
import { PROVIDER_LABELS } from "../credentials/credential-form";

import type { RouteFormValues } from "./route-form";

interface CredentialSelectProps {
  index: number;
  credentials: readonly Credential[];
  /** Set by `FormField`: the label's target and the hint or error it points at. */
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

/**
 * The org's credentials with their provider as meta. A target whose credential is not in the
 * list (deleted or not loaded) keeps its value and reads as unknown.
 */
export function CredentialSelect({ index, credentials, ...aria }: CredentialSelectProps) {
  const { control } = useFormContext<RouteFormValues>();
  return (
    <Controller
      control={control}
      name={`targets.${index}.credential_id`}
      render={({ field }) => {
        const known =
          field.value === "" || credentials.some((credential) => credential.id === field.value);
        return (
          <Select value={field.value} onValueChange={field.onChange}>
            <SelectTrigger
              ref={field.ref}
              onBlur={field.onBlur}
              className="aria-invalid:border-[1.5px] aria-invalid:border-danger"
              {...aria}
            >
              <SelectValue placeholder="Pick a credential" />
            </SelectTrigger>
            <SelectContent>
              {known ? null : <SelectItem value={field.value}>Unknown credential</SelectItem>}
              {credentials.map((credential) => (
                <SelectItem key={credential.id} value={credential.id}>
                  <span className="flex items-center gap-2">
                    <span className="truncate">{credential.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {PROVIDER_LABELS[credential.provider]}
                    </span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        );
      }}
    />
  );
}
