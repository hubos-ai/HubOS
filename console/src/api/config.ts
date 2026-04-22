declare const VITE_API_BASE_URL: string;
declare const TOKEN: string;

const AUTH_TOKEN_KEY = "hubos_auth_token";

/**
 * Get the full API URL with /api prefix
 * @param path - API path (e.g., "/models", "/skills", or already "/api/models")
 * @returns Full API URL (e.g., "http://localhost:8088/api/models" or "/api/models")
 *
 * Behavior:
 * - If path already starts with /api/ or is exactly /api: assume it's the full backend
 *   path (from adapters that pass /api/... directly). Don't add another /api prefix.
 * - Otherwise: prepend /api prefix as usual.
 */
export function getApiUrl(path: string): string {
  const base = VITE_API_BASE_URL || "";
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;

  // If path already starts with /api, it's already the full backend path
  if (normalizedPath.startsWith("/api/") || normalizedPath === "/api") {
    return `${base}${normalizedPath}`;
  }

  // Otherwise prepend /api
  return `${base}/api${normalizedPath}`;
}

/**
 * Get the API token - checks localStorage first (auth login),
 * then falls back to the build-time TOKEN constant.
 * @returns API token string or empty string
 */
export function getApiToken(): string {
  const stored = localStorage.getItem(AUTH_TOKEN_KEY);
  if (stored) return stored;
  return typeof TOKEN !== "undefined" ? TOKEN : "";
}

/**
 * Store the auth token in localStorage after login.
 */
export function setAuthToken(token: string): void {
  localStorage.setItem(AUTH_TOKEN_KEY, token);
}

/**
 * Remove the auth token from localStorage (logout / 401).
 */
export function clearAuthToken(): void {
  localStorage.removeItem(AUTH_TOKEN_KEY);
}
