import { API_PREFIX, api } from "./client";
import type {
  Accepted,
  AuthSession,
  InviteAcceptance,
  InvitePreview,
  LoginOut,
  Me,
  User,
} from "./types";

export interface Credentials {
  email: string;
  password: string;
}

export interface SignupInput extends Credentials {
  name: string;
}

export interface PasswordResetInput {
  /** The secret from the emailed link, as read from the URL fragment. */
  token: string;
  password: string;
}

export const authApi = {
  me: (): Promise<Me> => api.get<Me>(`${API_PREFIX}/auth/me`),
  /**
   * Resolves with `signed_in` (the session cookies are set) or `totp_required`: the password was
   * right but no one is signed in yet, and the challenge goes to `securityApi.totpVerify` with a
   * code before it expires.
   */
  login: (input: Credentials): Promise<LoginOut> =>
    api.post<LoginOut>(`${API_PREFIX}/auth/login`, input),
  signup: (input: SignupInput): Promise<User> => api.post<User>(`${API_PREFIX}/auth/signup`, input),
  logout: (): Promise<void> => api.post<undefined>(`${API_PREFIX}/auth/logout`),
  demoSession: (): Promise<unknown> => api.post<unknown>(`${API_PREFIX}/demo/session`),

  /** Emails a reset link if the address has an account; the answer never says whether it does. */
  forgotPassword: (email: string): Promise<Accepted> =>
    api.post<Accepted>(`${API_PREFIX}/auth/password/forgot`, { email }),
  /** Ends every session of the account; nobody is signed in afterwards. */
  resetPassword: (input: PasswordResetInput): Promise<void> =>
    api.post<undefined>(`${API_PREFIX}/auth/password/reset`, input),

  /** Mails the signed-in user a fresh verification link, at most 3 per hour. */
  requestEmailVerification: (): Promise<Accepted> =>
    api.post<Accepted>(`${API_PREFIX}/auth/email/verify/request`),
  /** Needs no session: the link is often opened in a browser that is not signed in. */
  confirmEmailVerification: (token: string): Promise<User> =>
    api.post<User>(`${API_PREFIX}/auth/email/verify/confirm`, { token }),

  /** The caller's own setting, across every project; the demo account is `403 FORBIDDEN`. */
  setActivationChecklist: (dismissed: boolean): Promise<void> =>
    api.put<undefined>(`${API_PREFIX}/auth/me/activation-checklist`, { dismissed }),

  sessions: (): Promise<AuthSession[]> => api.get<AuthSession[]>(`${API_PREFIX}/auth/sessions`),
  revokeSession: (sessionId: string): Promise<void> =>
    api.delete(`${API_PREFIX}/auth/sessions/${encodeURIComponent(sessionId)}`),
  /** Signs out everywhere else: ends every session except the one making the call. */
  revokeOtherSessions: (): Promise<void> => api.delete(`${API_PREFIX}/auth/sessions`),

  previewInvite: (token: string): Promise<InvitePreview> =>
    api.get<InvitePreview>(`${API_PREFIX}/invites/preview`, { token }),
  acceptInvite: (token: string): Promise<InviteAcceptance> =>
    api.post<InviteAcceptance>(`${API_PREFIX}/invites/accept`, { token }),
};
