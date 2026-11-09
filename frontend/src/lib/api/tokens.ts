import { API_PREFIX, api } from "./client";
import type { CreatedPersonalAccessToken, PersonalAccessToken, TokenScope } from "./types";

export interface CreateTokenInput {
  name: string;
  /** Required: how much a token may do is decided each time. */
  scope: TokenScope;
  /** An ISO time in the future, or null for a token that never expires. */
  expires_at: string | null;
  password?: string;
  code?: string;
}

const TOKENS_PATH = `${API_PREFIX}/auth/tokens`;

export const tokensApi = {
  /** Only tokens that can still be used: revoked and expired ones are not listed. */
  list: (): Promise<PersonalAccessToken[]> => api.get<PersonalAccessToken[]>(TOKENS_PATH),
  /** The answer carries the whole token, once. A session is required, a token cannot mint one. */
  create: (input: CreateTokenInput): Promise<CreatedPersonalAccessToken> =>
    api.post<CreatedPersonalAccessToken>(TOKENS_PATH, input),
  revoke: (tokenId: string): Promise<void> =>
    api.delete(`${TOKENS_PATH}/${encodeURIComponent(tokenId)}`),
};
