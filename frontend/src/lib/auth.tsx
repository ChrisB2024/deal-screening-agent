import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from "react";

const API_BASE = "/api/v1";

interface User {
  user_id: string;
  tenant_id: string;
  email: string;
}

interface AuthState {
  user: User | null;
  accessToken: string | null;
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  loading: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const STORAGE_KEY = "auth";

function loadStored(): { accessToken: string; refreshToken: string; user: User } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function saveTokens(accessToken: string, refreshToken: string, user: User) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ accessToken, refreshToken, user }));
}

function clearTokens() {
  localStorage.removeItem(STORAGE_KEY);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ user: null, accessToken: null });
  const [loading, setLoading] = useState(true);

  // On mount, try to restore session
  useEffect(() => {
    const stored = loadStored();
    if (stored) {
      setState({ user: stored.user, accessToken: stored.accessToken });
    }
    setLoading(false);
  }, []);

  const handleTokenResponse = useCallback((data: {
    access_token: string;
    refresh_token: string;
    user: User;
  }) => {
    saveTokens(data.access_token, data.refresh_token, data.user);
    setState({ user: data.user, accessToken: data.access_token });
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail?.message || body.detail || "Login failed");
    }
    handleTokenResponse(await res.json());
  }, [handleTokenResponse]);

  const register = useCallback(async (email: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Registration failed");
    }
    handleTokenResponse(await res.json());
  }, [handleTokenResponse]);

  const logout = useCallback(async () => {
    const stored = loadStored();
    if (stored) {
      await fetch(`${API_BASE}/auth/logout`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${stored.accessToken}`,
        },
        body: JSON.stringify({ refresh_token: stored.refreshToken }),
      }).catch(() => {});
    }
    clearTokens();
    setState({ user: null, accessToken: null });
  }, []);

  return (
    <AuthContext.Provider value={{ ...state, login, register, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

/**
 * Get a valid access token, refreshing if needed.
 * Called by api.ts before each request.
 */
export async function getAccessToken(): Promise<string | null> {
  const stored = loadStored();
  if (!stored) return null;

  // Decode JWT to check expiry (access tokens are short-lived)
  try {
    const payload = JSON.parse(atob(stored.accessToken.split(".")[1]));
    const expiresAt = payload.exp * 1000;
    // If token expires in more than 60s, it's still good
    if (expiresAt - Date.now() > 60_000) {
      return stored.accessToken;
    }
  } catch {
    // Can't decode — try refresh
  }

  // Try to refresh
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: stored.refreshToken }),
    });
    if (!res.ok) {
      clearTokens();
      return null;
    }
    const data = await res.json();
    saveTokens(data.access_token, data.refresh_token, data.user);
    return data.access_token;
  } catch {
    clearTokens();
    return null;
  }
}
