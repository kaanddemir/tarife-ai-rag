import type { Citation, Package } from "./api";

export type Role = "user" | "assistant";

export interface Message {
  role: Role;
  content: string;
  citations?: Citation[];
  packages?: Package[];
  error?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  messages: Message[];
}
