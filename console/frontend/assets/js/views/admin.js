import { api, toast, statusEl, esc, fmtWhen, fmtDuration } from "../api.js";

export default {
  async render(root) {
    root.innerHTML = `<div class="page-head"><div><h1>Admin</h1><div class="sub">Platform-wide state. All actions are audited.</div></div></div>
      <div class="stat-grid" id="ad-stats"></div>
      <div class="tabs" id="ad-tabs"></div>
      <div id="ad-body"></div>`;

    let tab = "instances";
    const tabsEl = root.querySelector("#ad-tabs");
    const body = root.querySelector("#ad-body");
    const TABS = ["instances", "users", "workers", "deployments", "system"];

    async function loadStats() {
      try {
        const s = await api.get("/api/admin/overview");
        root.querySelector("#ad-stats").innerHTML = `
          <div class="stat"><div class="label">Users</div><div class="value">${s.users}</div></div>
          <div class="stat"><div class="label">Instances</div><div class="value">${s.instances}</div></div>
          <div class="stat"><div class="label">Running</div><div class="value" style="color:var(--green)">${s.instances_running}</div></div>
          <div class="stat"><div class="label">Workers online</div><div class="value">${s.workers_online}</div></div>`;
      } catch (err) {
        if (err.status === 403) location.hash = "#/";
      }
    }

    function renderTabs() {
      tabsEl.innerHTML = TABS.map(
        (t) => `<button class="tab ${t === tab ? "active" : ""}" data-tab="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`
      ).join("");
      tabsEl.querySelectorAll(".tab").forEach((b) =>
        b.addEventListener("click", () => {
          tab = b.dataset.tab;
          renderTabs();
          renderTab();
        })
      );
    }

    async function renderTab() {
      if (tab === "instances") return renderInstances();
      if (tab === "users") return renderUsers();
      if (tab === "workers") return renderWorkers();
      if (tab === "deployments") return renderDeployments();
      if (tab === "system") return renderSystem();
    }

    async function renderInstances() {
      const { instances } = await api.get("/api/admin/instances");
      body.innerHTML = `
        <div class="card" style="padding:0;overflow-x:auto">
        <table class="table">
          <thead><tr><th>Instance</th><th>Owner</th><th>Status</th><th>Provider</th><th>Created</th><th>Actions</th></tr></thead>
          <tbody>
            ${instances.length === 0 ? `<tr><td colspan="6" class="faint" style="text-align:center;padding:24px">None.</td></tr>` : ""}
            ${instances
              .map(
                (i) => `
              <tr data-id="${i.id}">
                <td><strong>${esc(i.name)}</strong><div class="faint mono-sm">${esc(i.slug)}</div></td>
                <td>@${esc(i.owner_login)}</td>
                <td>${statusEl(i.status).outerHTML}</td>
                <td class="mono-sm">${esc(i.provider || "auto")}</td>
                <td class="faint">${fmtWhen(i.created_at)}</td>
                <td>
                  <div class="row" style="gap:6px">
                    <button class="btn sm" data-act="restart">Restart</button>
                    <button class="btn sm" data-act="stop">Stop</button>
                    <button class="btn sm" data-act="redeploy">Redeploy</button>
                    <button class="btn sm danger" data-act="delete">Delete</button>
                  </div>
                </td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table></div>`;
      body.querySelectorAll("tr[data-id] .btn").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const id = btn.closest("tr").dataset.id;
          const act = btn.dataset.act;
          try {
            if (act === "delete") {
              if (!confirm("Delete this instance?")) return;
              await api.delete(`/api/admin/instances/${id}`);
            } else {
              await api.post(`/api/admin/instances/${id}/actions/${act}`);
            }
            toast(`${act} done`, "ok");
            renderTab();
          } catch (err) {
            toast(err.message, "err");
          }
        })
      );
    }

    async function renderUsers() {
      const { users } = await api.get("/api/admin/users");
      body.innerHTML = `
        <div class="card" style="padding:0;overflow-x:auto">
        <table class="table">
          <thead><tr><th>User</th><th>Instances</th><th>Admin</th><th>Joined</th><th>Last login</th><th>Actions</th></tr></thead>
          <tbody>
            ${users
              .map(
                (u) => `
              <tr data-id="${u.id}">
                <td><strong>${esc(u.name || u.login)}</strong> <span class="faint">@${esc(u.login)}</span></td>
                <td>${u.instance_count}</td>
                <td>${u.is_admin ? `<span class="chip">admin</span>` : ""}${u.is_disabled ? ` <span class="chip" style="color:var(--red)">disabled</span>` : ""}</td>
                <td class="faint">${fmtWhen(u.created_at)}</td>
                <td class="faint">${fmtWhen(u.last_login_at)}</td>
                <td>
                  <div class="row" style="gap:6px">
                    <button class="btn sm" data-act="toggle-admin">${u.is_admin ? "Revoke admin" : "Make admin"}</button>
                    <button class="btn sm ${u.is_disabled ? "" : "danger"}" data-act="toggle-disable">${u.is_disabled ? "Enable" : "Disable"}</button>
                  </div>
                </td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table></div>`;
      body.querySelectorAll("tr[data-id] .btn").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const id = btn.closest("tr").dataset.id;
          const act = btn.dataset.act;
          const patch =
            act === "toggle-admin" ? { is_admin: btn.textContent.startsWith("Make") } : { is_disabled: !btn.textContent.startsWith("Enable") };
          try {
            await api.patch(`/api/admin/users/${id}`, patch);
            toast("Updated", "ok");
            renderTab();
          } catch (err) {
            toast(err.message, "err");
          }
        })
      );
    }

    async function renderWorkers() {
      const { workers } = await api.get("/api/admin/workers");
      body.innerHTML = `
        <div class="card" style="padding:0;overflow-x:auto">
        <table class="table">
          <thead><tr><th>Node</th><th>Region</th><th>Status</th><th>Driver</th><th>CPU</th><th>Memory</th><th>Capacity</th><th>Heartbeat</th><th></th></tr></thead>
          <tbody>
            ${workers.length === 0 ? `<tr><td colspan="9" class="faint" style="text-align:center;padding:24px">No workers have reported yet.</td></tr>` : ""}
            ${workers
              .map(
                (w) => `
              <tr data-id="${w.id}">
                <td class="mono-sm">${esc(w.node_id)}</td>
                <td>${esc(w.region)}</td>
                <td>${statusEl(w.enabled ? (w.status === "online" ? "running" : "stopped") : "failed").outerHTML}</td>
                <td class="mono-sm">${esc(w.driver)}</td>
                <td>${w.cpu_percent != null ? w.cpu_percent.toFixed(0) + "%" : "—"}</td>
                <td>${w.mem_used_mb != null ? `${w.mem_used_mb} / ${w.mem_total_mb} MB` : "—"}</td>
                <td>${w.instances ?? 0} / ${w.capacity ?? "?"}</td>
                <td class="faint">${fmtWhen(w.last_heartbeat)}</td>
                <td><button class="btn sm" data-act="toggle">${w.enabled ? "Disable" : "Enable"}</button></td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table></div>`;
      body.querySelectorAll("tr[data-id] .btn").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const id = btn.closest("tr").dataset.id;
          const enabled = btn.textContent.trim() === "Disable" ? false : true;
          try {
            await api.patch(`/api/admin/workers/${id}`, { enabled });
            toast("Worker updated", "ok");
            renderTab();
          } catch (err) {
            toast(err.message, "err");
          }
        })
      );
    }

    async function renderDeployments() {
      const { deployments } = await api.get("/api/admin/deployments");
      body.innerHTML = `
        <div class="card" style="padding:0;overflow-x:auto">
        <table class="table">
          <thead><tr><th>Instance</th><th>Version</th><th>Status</th><th>Started</th><th>Duration</th><th>Error</th></tr></thead>
          <tbody>
            ${deployments.length === 0 ? `<tr><td colspan="6" class="faint" style="text-align:center;padding:24px">No deployments yet.</td></tr>` : ""}
            ${deployments
              .map(
                (d) => `
              <tr>
                <td>${esc(d.instance_name)}</td>
                <td class="mono-sm">v${d.version}</td>
                <td>${statusEl(d.status).outerHTML}</td>
                <td class="faint">${fmtWhen(d.started_at)}</td>
                <td>${fmtDuration(d.duration_ms)}</td>
                <td class="faint" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(d.error || "")}</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table></div>`;
    }

    async function renderSystem() {
      const info = await api.get("/api/admin/system");
      body.innerHTML = `
        <div class="card" style="max-width:560px">
          <h3>System</h3>
          <table class="review-table section-gap">
            <tr><td>Database</td><td>${info.database.ok ? "✅ PostgreSQL" : "❌ down"}</td></tr>
            <tr><td>Railway provider</td><td>${info.provider.railway ? "configured" : "not configured"}</td></tr>
            <tr><td>Local worker</td><td class="mono-sm">${esc(info.provider.local_worker)}</td></tr>
            <tr><td>GitHub OAuth</td><td>${info.github_oauth ? "configured" : "not configured"}</td></tr>
          </table>
        </div>`;
    }

    await loadStats();
    renderTabs();
    await renderTab();
  },
};
