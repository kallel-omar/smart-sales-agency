import { createContext, useContext, useEffect, useMemo, useState } from "react";

import { apiClient } from "../lib/api";
import { tokenStorage, workspaceStorage } from "../lib/storage";
import type { UserRead } from "../types/api";

type AuthStatus = "checking" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: AuthStatus;
  token: string | null;
  user: UserRead | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() => tokenStorage.get());
  const [user, setUser] = useState<UserRead | null>(null);
  const [status, setStatus] = useState<AuthStatus>(token ? "checking" : "unauthenticated");

  const logout = () => {
    tokenStorage.clear();
    workspaceStorage.clear();
    setToken(null);
    setUser(null);
    setStatus("unauthenticated");
  };

  useEffect(() => {
    const onExpired = () => logout();
    window.addEventListener("hiri:auth-expired", onExpired);
    return () => window.removeEventListener("hiri:auth-expired", onExpired);
  }, []);

  useEffect(() => {
    if (!token) {
      setStatus("unauthenticated");
      return;
    }
    let active = true;
    setStatus("checking");
    apiClient
      .me(token)
      .then((currentUser) => {
        if (!active) {
          return;
        }
        setUser(currentUser);
        setStatus("authenticated");
      })
      .catch(() => {
        if (active) {
          logout();
        }
      });
    return () => {
      active = false;
    };
  }, [token]);

  const login = async (email: string, password: string) => {
    const response = await apiClient.login(email, password);
    tokenStorage.set(response.access_token);
    setToken(response.access_token);
  };

  const value = useMemo(
    () => ({ status, token, user, login, logout }),
    [status, token, user]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
