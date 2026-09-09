import { api, setCsrf } from "./api.js";
import dashboard from "./views/dashboard.js";
import wizard from "./views/wizard.js";
import instance from "./views/instance.js";
import admin from "./views/admin.js";
import { requireAuth } from "./views/login.js";
import mark from "./mark.js";

export const state = { user: null };

const routes = [
  { path: /^\/$/, view: dashboard, nav: "dashboard" },
  { path: /^\/instances\/new$/, view: wizard, nav: "new" },
  { path: /^\/instances\/([0-9a-f-]{36})$/, view: instance, nav: "dashboard" },
  { path: /^\/admin$/, view: admin, nav: "admin", adminOnly: true },
];

function icons(name) {
  const paths = {
    dashboard: `<path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/>`,
    instances: `<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/>`,
    plus: `<path d="M12 5v14M5 12h14"/>`,
    admin: `<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/>`,
  };
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths[name]}</svg>`;
}

function renderShell(nav) {
  const app = document.getElementById("app");
  const u = state.user;
  app.innerHTML = `
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark" style="color:var(--accent)">${mark}</span>
        <div><div class="brand-name">Lunel</div><div class="brand-sub">Console</div></div>
      </div>
      <a class="nav-item ${nav === "dashboard" ? "active" : ""}" href="#/" data-nav="dashboard">${icons("dashboard")} Dashboard</a>
      <a class="nav-item ${nav === "new" ? "active" : ""}" href="#/instances/new" data-nav="new">${icons("plus")} Create Instance</a>
      ${u.is_admin ? `<div class="nav-sep"></div><a class="nav-item ${nav === "admin" ? "active" : ""}" href="#/admin" data-nav="admin">${icons("admin")} Admin</a>` : ""}
      <div class="sidebar-footer">
        ${u.avatar_url ? `<img src="${u.avatar_url}" alt="" referrerpolicy="no-referrer">` : ""}
        <div class="who"><div class="name">${u.name || u.login}</div><div class="sub">@${u.login}</div></div>
        <button class="btn sm ghost right" id="logout-btn">Sign out</button>
      </div>
    </aside>
    <div class="main">
      <div class="topbar">
        <div class="brand">
          <span class="brand-mark" style="color:var(--accent)">${mark}</span>
          <div><div class="brand-name">Lunel</div></div>
        </div>
        <button class="btn sm ghost right" id="logout-btn-m">Sign out</button>
      </div>
      <div class="content" id="view"></div>
      <nav class="bottomnav">
        <a href="#/" data-nav="dashboard">${icons("dashboard")}<span>Dashboard</span></a>
        <a href="#/instances/new" data-nav="new">${icons("plus")}<span>Create</span></a>
        ${u.is_admin ? `<a href="#/admin" data-nav="admin">${icons("admin")}<span>Admin</span></a>` : ""}
      </nav>
    </div>
  </div>`;
  document.getElementById("logout-btn").addEventListener("click", logout);
  const mBtn = document.getElementById("logout-btn-m");
  if (mBtn) mBtn.addEventListener("click", logout);
  highlightNav(nav);
}

function highlightNav(nav) {
  document.querySelectorAll("[data-nav]").forEach((el) => {
    el.classList.toggle("active", el.dataset.nav === nav);
  });
}

async function logout() {
  await api.post("/auth/logout");
  location.href = "/";
}

let activeCleanup = null;

async function render() {
  const hash = location.hash.replace(/^#/, "") || "/";
  for (const route of routes) {
    const match = hash.match(route.path);
    if (!match) continue;
    if (route.adminOnly && !state.user.is_admin) {
      location.hash = "#/";
      return;
    }
    const params = match.slice(1);
    renderShell(route.nav);
    const view = document.getElementById("view");
    if (activeCleanup) activeCleanup();
    activeCleanup = await route.view.render(view, ...params);
    return;
  }
  location.hash = "#/";
}

export function navigate(hash) {
  location.hash = hash;
}

export function setCleanup(fn) {
  activeCleanup = fn;
}

async function boot() {
  const me = await api.get("/auth/me");
  if (!me.authenticated) {
    requireAuth(document.getElementById("app"));
    return;
  }
  state.user = me.user;
  setCsrf(me.csrf_token);
  window.addEventListener("hashchange", render);
  await render();
}

boot().catch((err) => {
  document.getElementById("app").innerHTML = `<div class="login-wrap"><div class="login-card">
    <div class="card"><h3>Lunel Console failed to load</h3>
    <p class="muted">${String(err.message || err)}</p></div></div></div>`;
});
