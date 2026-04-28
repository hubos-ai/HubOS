/**
 * Dev Auth Adapter
 *
 * Provides fake auth responses when VITE_DEV_AUTH_BYPASS=true.
 * This lets HubOS console run against HubOS backend that has no /auth/* routes.
 *
 * Only used for local development against HubOS 8012.
 */

import { setAuthToken } from "../config";

declare const VITE_DEV_AUTH_BYPASS: string;

const DEV_BYPASS =
  typeof VITE_DEV_AUTH_BYPASS === "string" && VITE_DEV_AUTH_BYPASS === "true";
const DEV_TOKEN = "dev-bypass-token-0000";

export const devAuthApi = {
  login: async (_username: string, _password: string) => {
    if (!DEV_BYPASS) {
      throw new Error("Dev auth bypass not enabled");
    }
    setAuthToken(DEV_TOKEN);
    return { token: DEV_TOKEN, username: _username, message: "dev bypass" };
  },

  register: async (username: string, _password: string) => {
    if (!DEV_BYPASS) {
      throw new Error("Dev auth bypass not enabled");
    }
    setAuthToken(DEV_TOKEN);
    return { token: DEV_TOKEN, username, message: "dev bypass" };
  },

  getStatus: async () => {
    if (!DEV_BYPASS) {
      throw new Error("Dev auth bypass not enabled");
    }
    // HubOS auth is always "enabled" in dev too — but we bypass the verify step in AuthGuard
    return { enabled: true, has_users: true };
  },

  updateProfile: async (
    _currentPassword: string,
    newUsername?: string,
    _newPassword?: string,
  ) => {
    if (!DEV_BYPASS) {
      throw new Error("Dev auth bypass not enabled");
    }
    return {
      token: DEV_TOKEN,
      username: newUsername || "dev-user",
      message: "dev bypass",
    };
  },
};

export const isDevBypass = DEV_BYPASS;
