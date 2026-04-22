// Intentionally minimal. We don't expose any privileged API to the renderer:
// the wrapped web UI already talks to the local backend via its own HTTP
// endpoints, so the desktop shell has nothing of value to bridge.
//
// Kept as a file so BrowserWindow.webPreferences.preload has a stable target
// if we want to expose something later (e.g. for native file pickers that
// bypass the browser's sandbox).
