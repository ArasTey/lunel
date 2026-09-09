import { api, statusEl, fmtWhen, esc, toast, copyButton, TRANSITION_STATES } from "../api.js";
import { setCleanup } from "../app.js";

export default {
  async render(root) {
    root.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Dashboard</h1>
          <div class="sub">Your Lunel instances at a glance.</div>
        </div>
        <div class="head-actions">
          <a class="btn primary" href="#/instances/new" id="cta-create">+ Create Instance</a>
        </div>
      </div>
      <div class="stat-grid" id="stats"></div>
      <h3 style="margin:0 0 12px;font-size:14px">Instances</h3>
      <div id="inst-list"></div>
      <div class="card section-gap">
        <h3>Recent activity</h3>
        <div class="activity-feed" id="activity"></div>
      </div>`;

    let pollTimer = null;

    async function load() {
      const [{ instances }, { activity }] = await Promise.all([
        api.get("/api/instances"),
        api.get("/api/activity"),
      ]);

      const running = instances.filter((i) => i.status === "running").length;
      const stopped = instances.filter((i) => ["stopped", "deleted"].includes(i.status)).length;
      const failed = instances.filter((i) => i.status === "failed").length;
      const transitioning = instances.filter((i) => TRANSITION_STATES.has(i.status)).length;

      document.getElementById("stats").innerHTML = `
        <div class="stat"><div class="label">Active instances</div><div class="value">${instances.length}</div></div>
        <div class="stat"><div class="label">Running</div><div class="value" style="color:var(--green)">${running}</div></div>
        <div class="stat"><div class="label">Stopped</div><div class="value">${stopped}</div></div>
        <div class="stat"><div class="label">Failed</div><div class="value" style="color:${failed ? "var(--red)" : "inherit"}">${failed}</div></div>`;

      const list = document.getElementById("inst-list");
      if (instances.length === 0) {
        list.innerHTML = `
          <div class="empty">
            <div class="big">No instances yet</div>
            <div>Deploy your first Lunel Core instance in under a minute.</div>
            <div style="margin-top:16px"><a class="btn primary" href="#/instances/new">Create your first instance</a></div>
          </div>`;
      } else {
        list.innerHTML = `<div class="inst-grid">${instances.map((i) => instCard(i)).join("")}</div>`;
        list.querySelectorAll(".inst-card").forEach((card) => {
          card.addEventListener("click", () => {
            location.hash = `#/instances/${card.dataset.id}`;
          });
        });
        list.querySelectorAll(".copy-btn").forEach((btn) => {
          btn.addEventListener("click", (e) => e.stopPropagation());
        });
      }

      const feed = document.getElementById("activity");
      feed.innerHTML =
        activity.length === 0
          ? `<div class="faint" style="padding:10px 2px">Nothing yet.</div>`
          : activity
              .slice(0, 8)
              .map(
                (a) => `
            <div class="activity-item">
              <span class="when">${fmtWhen(a.ts)}</span>
              <span class="msg">${esc(a.message)}</span>
            </div>`
              )
              .join("");
    }

    function instCard(i) {
      const endpoint = i.domain
        ? (i.endpoint_url || `https://${i.domain}`)
        : null;
      return `
        <div class="inst-card" data-id="${i.id}">
          <div class="top">
            <span class="name">${esc(i.name)}</span>
            ${statusEl(i.status).outerHTML}
          </div>
          ${
            endpoint
              ? `<div class="endpoint"><span>${esc(endpoint)}</span>${copyButton(endpoint).outerHTML}</div>`
              : `<div class="endpoint faint"><span>no endpoint yet</span></div>`
          }
          <div class="meta">
            <span>${esc(i.region)}</span>
            <span>${i.deployments_count} deploys</span>
            <span>created ${fmtWhen(i.created_at)}</span>
          </div>
        </div>`;
    }

    await load();

    // Poll while any instance is transitioning so statuses stay honest.
    pollTimer = setInterval(async () => {
      try {
        const { instances } = await api.get("/api/instances");
        if (instances.some((i) => TRANSITION_STATES.has(i.status))) {
          await load();
        }
      } catch {
        /* transient */
      }
    }, 4000);
    setCleanup(() => clearInterval(pollTimer));

    root.querySelector("#cta-create").addEventListener("click", () => {});
    return () => clearInterval(pollTimer);
  },
};
