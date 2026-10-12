import type { Citation, Package } from "./api";

export type Role = "user" | "assistant";

export interface Message {
  role: Role;
  content: string;
  citations?: Citation[];
  packages?: Package[];
  error?: boolean;
  /** For an error bubble: what kind of failure, so the UI can offer the right action. */
  errorKind?: "quota" | "auth" | "network" | "timeout" | "server";
  /** When the message was created (ms). Missing on messages saved by older versions. */
  at?: number;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  messages: Message[];
}
