import { getRouteApi, Link } from "@tanstack/react-router";
import { ArrowRight, Loader2 } from "lucide-react";

import { AUTH_LINK, AUTH_NOTE, Button } from "./auth-controls";
import { AuthFooter } from "./auth-footer";
import { AuthLayout } from "./auth-layout";
import { LoginForm } from "./login-form";
import { LoginOAuthError } from "./login-oauth-error";
import { OAuthButtons } from "./oauth-buttons";
import { useDemoSession } from "./use-demo-session";

const routeApi = getRouteApi("/login");

export function LoginPage() {
  const { next, error } = routeApi.useSearch();
  const demo = useDemoSession();

  return (
    <AuthLayout
      title="Welcome"
      accent="back."
      description="Observe your LLM calls, costs and errors."
    >
      <LoginOAuthError code={error} />
      <OAuthButtons next={next} dividerLabel="or sign in with email" />
      <LoginForm next={next} />

      <div className="flex flex-col gap-3.5">
        <AuthFooter>
          New here?
          <Link to="/signup" search={{ next }} className={AUTH_LINK}>
            Create an account
          </Link>
        </AuthFooter>
        <div className="flex flex-col items-start gap-1 border-t border-mono-line pt-3.5">
          {/* The spinner takes the arrow's place, so the line keeps its width while it loads. */}
          <Button
            variant="link"
            className="min-h-11 gap-1.5 text-[15px] font-medium text-mono-900 no-underline hover:text-mono-600 hover:no-underline lg:min-h-0"
            disabled={demo.isPending}
            aria-busy={demo.isPending || undefined}
            onClick={() => {
              demo.mutate();
            }}
          >
            Try the live demo
            {demo.isPending ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <ArrowRight aria-hidden />
            )}
          </Button>
          <p className={AUTH_NOTE}>Read-only demo with real traffic. No account needed.</p>
        </div>
      </div>
    </AuthLayout>
  );
}
