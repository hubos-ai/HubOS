/**
 * Chat adapter — calls native HubOS backend directly.
 *
 * Native routes (prefix `/chats` under `/api`):
 * - GET    /api/chats                    → ChatSpec[]
 * - POST   /api/chats                    → ChatSpec
 * - GET    /api/chats/{chat_id}          → ChatHistory
 * - PUT    /api/chats/{chat_id}          → ChatSpec
 * - DELETE /api/chats/{chat_id}          → ChatDeleteResponse
 * - POST   /api/chats/batch-delete       body: string[]  → {deleted: number}
 * - POST   /api/console/chat/stop?chat_id=...
 * - POST   /api/console/upload           (multipart)
 *
 * Response shapes match the frontend types directly; this adapter is a thin
 * pass-through.
 */

import { request } from "../request";
import { getApiUrl, getApiToken } from "../config";
import { buildAuthHeaders } from "../authHeaders";
import type {
  ChatSpec,
  ChatHistory,
  ChatDeleteResponse,
} from "../types/chat";
import type { ChatUploadResponse } from "../modules/chat";

const FILES_PREVIEW = "/files/preview";

export interface ChatAdapter {
  listChats: (params?: { user_id?: string; channel?: string }) => Promise<ChatSpec[]>;
  createChat: (chat: Partial<ChatSpec>) => Promise<ChatSpec>;
  getChat: (chatId: string) => Promise<ChatHistory>;
  updateChat: (chatId: string, chat: Partial<ChatSpec>) => Promise<ChatSpec>;
  deleteChat: (chatId: string) => Promise<ChatDeleteResponse>;
  batchDeleteChats: (
    chatIds: string[],
  ) => Promise<{ success: boolean; deleted_count: number }>;
  stopChat: (chatId: string) => Promise<{ stopped: boolean }>;
  uploadFile: (file: File) => Promise<ChatUploadResponse>;
  filePreviewUrl: (filename: string) => string;
}

export const chatAdapter: ChatAdapter = {
  listChats: (params) => {
    const usp = new URLSearchParams();
    if (params?.user_id) usp.append("user_id", params.user_id);
    if (params?.channel) usp.append("channel", params.channel);
    const qs = usp.toString();
    return request<ChatSpec[]>(`/chats${qs ? `?${qs}` : ""}`);
  },

  createChat: (chat) =>
    request<ChatSpec>("/chats", {
      method: "POST",
      body: JSON.stringify(chat),
    }),

  getChat: (chatId) =>
    request<ChatHistory>(`/chats/${encodeURIComponent(chatId)}`),

  updateChat: (chatId, chat) =>
    request<ChatSpec>(`/chats/${encodeURIComponent(chatId)}`, {
      method: "PUT",
      body: JSON.stringify(chat),
    }),

  deleteChat: (chatId) =>
    request<ChatDeleteResponse>(
      `/chats/${encodeURIComponent(chatId)}`,
      { method: "DELETE" },
    ),

  batchDeleteChats: async (chatIds) => {
    const res = await request<{ deleted: number }>("/chats/batch-delete", {
      method: "POST",
      body: JSON.stringify(chatIds),
    });
    const count = typeof res?.deleted === "number" ? res.deleted : 0;
    return { success: count > 0 || chatIds.length === 0, deleted_count: count };
  },

  stopChat: (chatId) =>
    request<{ stopped: boolean }>(
      `/console/chat/stop?chat_id=${encodeURIComponent(chatId)}`,
      { method: "POST" },
    ),

  uploadFile: async (file) => {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(getApiUrl("/console/upload"), {
      method: "POST",
      headers: buildAuthHeaders(),
      body: formData,
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(
        `Upload failed: ${response.status} ${response.statusText}${text ? ` - ${text}` : ""}`,
      );
    }
    return response.json();
  },

  filePreviewUrl: (filename) => {
    if (!filename) return "";
    if (filename.startsWith("http://") || filename.startsWith("https://")) {
      return filename;
    }
    const path = `${FILES_PREVIEW}/${filename.replace(/^\/+/, "")}`;
    const url = getApiUrl(path);
    const token = getApiToken();
    if (token) return `${url}?token=${encodeURIComponent(token)}`;
    return url;
  },
};
