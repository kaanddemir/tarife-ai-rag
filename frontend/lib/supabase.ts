import { createClient } from "@supabase/supabase-js";

// Public (anon) Supabase credentials — safe to ship to the browser; row-level security
// and the backend's own JWT check are what actually protect data. Both are inlined at
// build time and must be set for the production build.
const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!url || !anonKey) {
  // Don't throw (that would crash the build's static prerender and blank the app in
  // dev); warn loudly instead. A placeholder keeps createClient from throwing on an
  // empty URL — auth simply won't work until the real env vars are set.
  console.error(
    "NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are not set — auth will not work."
  );
}

export const supabase = createClient(
  url || "https://placeholder.supabase.co",
  anonKey || "placeholder-anon-key",
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  }
);
