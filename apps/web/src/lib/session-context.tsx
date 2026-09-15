"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import type { SessionInfo } from "./api-types";
import { ApiError, apiRequest } from "./client-api";

interface SessionContextValue {
  session: SessionInfo;
  /** Mutation helper that adds the CSRF token and redirects to sign-in on 401. */
  mutate: <T>(path: string, options?: { method?: "POST" | "PATCH" | "DELETE"; body?: unknown; form?: FormData }) => Promise<T>;
  handleAuthError: (error: unknown) => boolean;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ session, children }: { session: SessionInfo; children: ReactNode }) {
  const router = useRouter();

  const handleAuthError = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) {
        router.replace("/login");
        router.refresh();
        return true;
      }
      return false;
    },
    [router],
  );

  const mutate = useCallback(
    async <T,>(
      path: string,
      options: { method?: "POST" | "PATCH" | "DELETE"; body?: unknown; form?: FormData } = {},
    ): Promise<T> => {
      try {
        return await apiRequest<T>(path, {
          method: options.method ?? "POST",
          body: options.body,
          form: options.form,
          csrfToken: session.csrf_token,
        });
      } catch (error) {
        handleAuthError(error);
        throw error;
      }
    },
    [session.csrf_token, handleAuthError],
  );

  const value = useMemo(() => ({ session, mutate, handleAuthError }), [session, mutate, handleAuthError]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) throw new Error("useSession must be used inside SessionProvider");
  return value;
}

export type Resource<T> =
  | { state: "loading"; data: T | null }
  | { state: "error"; data: T | null; error: unknown }
  | { state: "ready"; data: T };

/**
 * Loads a GET resource. Previously loaded data stays visible during reloads.
 * `path === null` disables loading.
 */
export function useResource<T>(path: string | null): Resource<T> & { reload: () => Promise<void> } {
  const [resource, setResource] = useState<Resource<T>>({ state: "loading", data: null });
  const { handleAuthError } = useContextSafe();

  const load = useCallback(async () => {
    if (path === null) return;
    setResource((current) => ({ state: "loading", data: current.data }));
    try {
      const data = await apiRequest<T>(path);
      setResource({ state: "ready", data });
    } catch (error) {
      if (!handleAuthError(error)) {
        setResource((current) => ({ state: "error", data: current.data, error }));
      }
    }
  }, [path, handleAuthError]);

  useEffect(() => {
    void load();
  }, [load]);

  return { ...resource, reload: load };
}

function useContextSafe(): Pick<SessionContextValue, "handleAuthError"> {
  const value = useContext(SessionContext);
  return value ?? { handleAuthError: () => false };
}
