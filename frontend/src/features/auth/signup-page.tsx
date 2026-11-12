import { getRouteApi, Link } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

import { AUTH_LINK, Button } from "./auth-controls";
import { AuthFooter } from "./auth-footer";
import { AuthLayout } from "./auth-layout";
import { OAuthButtons } from "./oauth-buttons";
import { SignupForm } from "./signup-form";
import { TermsLine } from "./terms-line";
import { useDemoSession } from "./use-demo-session";

const routeApi = getRouteApi("/signup");

export function SignupPage() {
  const { next } = routeApi.useSearch();
  const demo = useDemoSession();

  return (
    <AuthLayout
      title="Create an"
      accent="account."
      description="Start sending traces in a couple of minutes."
    >
      <OAuthButtons next={next} dividerLabel="or sign up with email" note={<TermsLine />} />
      <SignupForm next={next} />

      <div className="flex flex-col items-start gap-2.5">
        <AuthFooter>
          Already have an account?
          <Link to="/login" search={{ next }} className={AUTH_LINK}>
            Sign in
          </Link>
        </AuthFooter>
        {/* The spinner trails the label, so nothing moves while it loads. */}
        <Button
          variant="link"
          className={cn(AUTH_LINK, "min-h-11 gap-1.5 lg:min-h-0")}
          disabled={demo.isPending}
          aria-busy={demo.isPending || undefined}
          onClick={() => {
            demo.mutate();
          }}
        >
          Or try the live demo first
          {demo.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
        </Button>
      </div>
    </AuthLayout>
  );
}
