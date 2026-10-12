"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "./supabase";

interface AuthState {
  session: Session | null;
  /** True once we've resolved the initial session (or created a guest one). */
  ready: boolean;
  /** Signed in with a real identity (email magic link), not an anonymous guest. */
  isSignedIn: boolean;
  email: string | null;
  /** Send a passwordless magic link to the given email. */
  signInWithEmail: (email: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** Current access token; `refresh` forces one refresh attempt (after a 401). */
  getAccessToken: (refresh?: boolean) => Promise<string | null>;
}

const AuthContext = createContext<AuthState | null>(null);

function isAnonymous(session: Session | null): boolean {
  // Supabase flags guest sessions on the user object.
  return Boolean((session?.user as { is_anonymous?: boolean } | undefined)?.is_anonymous);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  // Resolves once the initial session exists (or creating one failed). Token requests
  // wait on it, so a message sent during bootstrap doesn't go out without a token
  // or race it into a second anonymous sign-in.
  const bootstrapRef = useRef<Promise<void> | null>(null);

  useEffect(() => {
    let active = true;

    async function bootstrap() {
      try {
        const { data } = await supabase.auth.getSession();
        if (!active) return;

        if (data.session) {
          setSession(data.session);
        } else {
          // No session yet → sign in anonymously so guests still get a real identity
          // (and JWT) the backend can count messages against.
          const { data: anon } = await supabase.auth.signInAnonymously();
          if (!active) return;
          setSession(anon.session ?? null);
        }
      } catch {
        // Network / storage failure: stay usable; the first send retries via refresh.
      } finally {
        if (active) setReady(true);
      }
    }

    bootstrapRef.current = bootstrap();

    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });

    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  const value = useMemo<AuthState>(() => {
    // Magic-link must return to the app's own base path. On a project-page deploy the
    // app lives under /TarifeAI, so origin alone (no path) would land on the wrong URL.
    const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
    const redirectTo =
      typeof window !== "undefined" ? `${window.location.origin}${base}/` : undefined;
    return {
      session,
      ready,
      isSignedIn: Boolean(session) && !isAnonymous(session),
      email: session?.user?.email ?? null,
      signInWithEmail: async (email: string) => {
        const { error } = await supabase.auth.signInWithOtp({
          email,
          options: { emailRedirectTo: redirectTo },
        });
        if (error) throw error;
      },
      signOut: async () => {
        await supabase.auth.signOut();
        // Drop back to a fresh guest session so the app stays usable after logout.
        const { data } = await supabase.auth.signInAnonymously();
        setSession(data.session ?? null);
      },
      getAccessToken: async (refresh = false) => {
        await bootstrapRef.current;
        try {
          if (!refresh) {
            const { data } = await supabase.auth.getSession();
            return data.session?.access_token ?? null;
          }
          // One attempt, no loop: the caller retries its request at most once.
          const { data, error } = await supabase.auth.refreshSession();
          if (!error && data.session) return data.session.access_token;
          const { data: current } = await supabase.auth.getSession();
          if (!current.session) {
            // No session at all (bootstrap failed or it was cleared): start a guest one.
            const { data: anon } = await supabase.auth.signInAnonymously();
            return anon.session?.access_token ?? null;
          }
          return null; // a dead signed-in session: the user has to sign in again
        } catch {
          return null;
        }
      },
    };
  }, [session, ready]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
