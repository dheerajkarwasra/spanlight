import { API_PREFIX, api, buildUrl } from "./client";
import type {
  LoginSignedIn,
  OAuthIdentity,
  OAuthProvider,
  OAuthProviderInfo,
  TotpEnabled,
  TotpSetup,
  TotpStatus,
} from "./types";

export interface TotpVerifyInput {
  /** The challenge from a `totp_required` login. */
  challenge: string;
  /** A six-digit code from the authenticator app, or a recovery code. */
  code: string;
}

export interface OAuthStartOptions {
  /** `sign_in` (the default) signs in or up; `link` adds the provider to the signed-in user. */
  intent?: "sign_in" | "link";
  /** Where to land afterwards: a path on this site. Defaults to `/` or `/settings/security`. */
  next?: string | undefined;
}

/**
 * The URL that starts signing in with (or linking) a provider. It is a browser navigation, not a
 * `fetch`: the server answers with a redirect to the provider's sign-in page.
 */
export function oauthStartUrl(provider: OAuthProvider, options: OAuthStartOptions = {}): string {
  const { intent = "sign_in", next } = options;
  return buildUrl(`${API_PREFIX}/auth/oauth/${provider}/start`, {
    intent: intent === "sign_in" ? undefined : intent,
    next,
  });
}

export const securityApi = {
  totpStatus: (): Promise<TotpStatus> => api.get<TotpStatus>(`${API_PREFIX}/auth/totp`),
  /**
   * Makes a secret for an authenticator app, replacing one from an earlier unconfirmed setup.
   * Needs the current password (`422 INVALID_PASSWORD`); an account without one must have signed
   * in within 10 minutes instead (`403 REAUTH_REQUIRED`).
   */
  totpSetup: (password?: string): Promise<TotpSetup> =>
    api.post<TotpSetup>(
      `${API_PREFIX}/auth/totp/setup`,
      password === undefined ? {} : { password },
    ),
  /** Turns it on with a code from the app. The recovery codes in the answer are shown once. */
  totpEnable: (code: string): Promise<TotpEnabled> =>
    api.post<TotpEnabled>(`${API_PREFIX}/auth/totp/enable`, { code }),
  /** Turns it off. Needs a valid code (authenticator or recovery), which is spent. */
  totpDisable: (code: string): Promise<void> =>
    api.post<undefined>(`${API_PREFIX}/auth/totp/disable`, { code }),
  /** The second step of signing in. Resolves with the user once the session cookies are set. */
  totpVerify: (input: TotpVerifyInput): Promise<LoginSignedIn> =>
    api.post<LoginSignedIn>(`${API_PREFIX}/auth/totp/verify`, input),

  /** The providers the operator has set up. */
  oauthProviders: (): Promise<OAuthProviderInfo[]> =>
    api.get<OAuthProviderInfo[]>(`${API_PREFIX}/auth/oauth/providers`),
  oauthIdentities: (): Promise<OAuthIdentity[]> =>
    api.get<OAuthIdentity[]>(`${API_PREFIX}/auth/oauth/identities`),
  /** `409 LAST_SIGN_IN_METHOD` when it is the only way to sign in. */
  unlinkOAuth: (provider: OAuthProvider): Promise<void> =>
    api.delete(`${API_PREFIX}/auth/oauth/${provider}`),
};
