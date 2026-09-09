// Lunel Console frontend utilities: API client with CSRF, toasts, formatting.
export let csrfToken = "";

export function setCsrf(token) {
  csrfToken = token || "";
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
  }
}

async function request(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken) headers["X-Lunel-CSRF"] = csrfToken;
  const resp = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 204) return {};
  let data = null;
  try {
    data = await resp.json();
  } catch {
    /* non-json */
  }
  if (!resp.ok) {
    if (resp.status === 401 && !path.startsWith("/auth")) {
      location.href = "/";
      throw new ApiError(401, "session expired");
    }
    throw new ApiError(resp.status, data && data.detail);
  }
  return data;
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b) => request("POST", p, b ?? {}),
  patch: (p, b) => request("PATCH", p, b ?? {}),
  delete: (p) => request("DELETE", p),
};

// ---- toasts ---------------------------------------------------------------
export function toast(message, kind = "info", ms = 3800) {
  let wrap = document.querySelector(".toast-wrap");
  if (!wrap) {
    wrap = document.createElement("div");
    wrap.className = "toast-wrap";
    document.body.appendChild(wrap);
  }
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// ---- formatting -----------------------------------------------------------
export function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(2)} MB`;
  return `${(n / 1073741824).toFixed(2)} GB`;
}

export function fmtUptime(seconds) {
  if (seconds == null) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function fmtWhen(iso) {
  if (!iso) return "—";
  const then = new Date(iso);
  const diff = (Date.now() - then.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function fmtDuration(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

export const STATUS_LABELS = {
  queued: "Queued",
  preparing: "Preparing",
  building: "Building",
  starting: "Starting",
  health_check: "Health check",
  running: "Running",
  failed: "Failed",
  stopping: "Stopping",
  stopped: "Stopped",
  deleted: "Deleted",
};

export const TRANSITION_STATES = new Set(["queued", "preparing", "building", "starting", "health_check", "stopping"]);

export function statusClass(status) {
  if (status === "running") return "running";
  if (["failed", "error", "crashed"].includes(status)) return "failed";
  if (["stopped", "deleted", "sleeping"].includes(status)) return "stopped";
  if (["stopping"].includes(status)) return "stopping";
  return "transition";
}

export function statusEl(status) {
  const span = document.createElement("span");
  span.className = `status ${statusClass(status)}`;
  span.innerHTML = `<span class="dot"></span>${STATUS_LABELS[status] || status}`;
  return span;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard", "ok", 1600);
  } catch {
    toast("Copy failed", "err");
  }
}

export function copyButton(text) {
  const btn = document.createElement("button");
  btn.className = "copy-btn";
  btn.title = "Copy";
  btn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5v-2a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 3.5v5A1.5 1.5 0 0 0 4 10h1.5"/></svg>`;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    copyText(text);
  });
  return btn;
}

export function esc(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

export function spinner() {
  return `<span class="spinner"></span>`;
}
