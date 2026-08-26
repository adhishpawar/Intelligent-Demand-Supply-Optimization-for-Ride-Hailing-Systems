import { createContext, useContext, useState, useCallback } from "react";
import { api, loadSession, saveSession, clearSession } from "../api/client";

// The single source of truth for "who is logged in and what role are they" — every
// role-specific screen (Rider/Driver/Admin) reads `role` from HERE, from the JWT the
// backend issued, never from a client-side toggle. This is what makes "role-based
// login and interface" real: the interface you see is a function of who you actually
// authenticated as, not a dropdown you can flip.
const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [session, setSession] = useState(() => loadSession());

  const requestOtp = useCallback(async (phone) => {
    return api.post("/v1/auth/otp/request", { phone });
  }, []);

  const verifyOtp = useCallback(async (phone, code) => {
    const resp = await api.post("/v1/auth/otp/verify", { phone, code });
    saveSession(resp);
    setSession(resp);
    return resp;
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setSession(null);
  }, []);

  const value = {
    session,
    role: session?.role || null,
    userId: session?.user_id || null,
    isAuthenticated: !!session,
    requestOtp,
    verifyOtp,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
