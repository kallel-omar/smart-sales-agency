const AUTH_TOKEN_KEY = "hiri.auth.accessToken";
const WORKSPACE_SLUG_KEY = "hiri.workspace.slug";

function safeStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export const tokenStorage = {
  get(): string | null {
    return safeStorage()?.getItem(AUTH_TOKEN_KEY) ?? null;
  },
  set(token: string): void {
    safeStorage()?.setItem(AUTH_TOKEN_KEY, token);
  },
  clear(): void {
    safeStorage()?.removeItem(AUTH_TOKEN_KEY);
  }
};

export const workspaceStorage = {
  get(): string | null {
    return safeStorage()?.getItem(WORKSPACE_SLUG_KEY) ?? null;
  },
  set(slug: string): void {
    safeStorage()?.setItem(WORKSPACE_SLUG_KEY, slug);
  },
  clear(): void {
    safeStorage()?.removeItem(WORKSPACE_SLUG_KEY);
  }
};
