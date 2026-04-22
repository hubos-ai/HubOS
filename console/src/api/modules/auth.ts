/**
 * Auth module — HubOS console auth against HubOS backend
 *
 * When VITE_DEV_AUTH_BYPASS=true, all auth calls return fake success responses.
 * This lets HubOS console run against HubOS 8012 which has no /auth/* routes.
 * Only for local development; token is "dev-bypass-token-0000".
 */

import { getApiUrl, setAuthToken } from "../config";

declare const VITE_DEV_AUTH_BYPASS: boolean;

const DEV_BYPASS = VITE_DEV_AUTH_BYPASS === true;
const DEV_TOKEN = "dev-bypass-token-0000";

export interface LoginResponse {
  token: string;
  username: string;
  message?: string;
}

export interface AuthStatusResponse {
  enabled: boolean;
  has_users: boolean;
}

export const authApi = {
  login: async (username: string, _password: string): Promise<LoginResponse> => {
    if (DEV_BYPASS) {
      setAuthToken(DEV_TOKEN);
      return { token: DEV_TOKEN, username, message: "dev bypass" };
    }
    const res = await fetch(getApiUrl("/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password: _password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Login failed");
    }
    return res.json();
  },

  register: async (
    username: string,
    password: string,
  ): Promise<LoginResponse> => {
    if (DEV_BYPASS) {
      setAuthToken(DEV_TOKEN);
      return { token: DEV_TOKEN, username, message: "dev bypass" };
    }
    const res = await fetch(getApiUrl("/auth/register"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Registration failed");
    }
    return res.json();
  },

  getStatus: async (): Promise<AuthStatusResponse> => {
    if (DEV_BYPASS) {
      return { enabled: true, has_users: true };
    }
    const res = await fetch(getApiUrl("/auth/status"));
    if (!res.ok) throw new Error("Failed to check auth status");
    return res.json();
  },

  updateProfile: async (
    _currentPassword: string,
    newUsername?: string,
    _newPassword?: string,
  ): Promise<LoginResponse> => {
    if (DEV_BYPASS) {
      return { token: DEV_TOKEN, username: newUsername || "dev-user", message: "dev bypass" };
    }
    const token = localStorage.getItem("hubos_auth_token") || "";
    const res = await fetch(getApiUrl("/auth/update-profile"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        current_password: _currentPassword,
        new_username: newUsername || null,
        new_password: _newPassword || null,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Update failed");
    }
    return res.json();
  },
};
