import { api, toast, statusEl, esc, fmtWhen, fmtBytes, fmtUptime, fmtDuration, copyButton, copyText, TRANSITION_STATES } from "../api.js";
import { setCleanup } from "../app.js";

const TABS = ["overview", "connections", "logs", "networking", "configuration", "activity", "settings"];

export default {
  async render(root, instanceId) {
    root.innerHTML = `<div class="muted">Loading instance…</div>`;
    let inst;
    try {
      inst = await api.get(`/api/instances/${instanceId}`);
    } catch (err) {
      root.innerHTML = `<div class="empty"><div class="big">Instance not found</div>${esc(err.message)}</div>`;
      return;
    }

    let tab = "overview";
    let pollTimer = null;

    root.innerHTML = `
      <div id="inst-header"></div>
      <div class="tabs" id="inst-tabs"></div>
      <div id="inst-body"></div>`;

    const header = root.querySelector("#inst-header");
    const tabsEl = root.querySelector("#inst-tabs");
    const body = root.querySelector("#inst-body");

    function renderHeader() {
      header.innerHTML = `
        <div class="page-head">
          <div>
            <div class="row" style="gap:12px">
              <h1 style="margin:0">${esc(inst.name)}</h1>
              <span id="inst-status">${statusEl(inst.status).outerHTML}</span>
            </div>
            <div class="sub" id="inst-endpoint"></div>
          </div>
          <div class="head-actions">
            <button class="btn" data-act="restart" id="a-restart">Restart</button>
            <button class="btn" data-act="stop" id="a-stop">Stop</button>
            <button class="btn" data-act="redeploy" id="a-redeploy">Redeploy</button>
            <button class="btn danger" data-act="delete" id="a-delete">Delete</button>
          </div>
        </div>`;
      const epEl = header.querySelector("#inst-endpoint");
      const pathDomain = (inst.domains || []).find((d) => d.kind === "path");
      if (pathDomain && pathDomain.url) {
        epEl.innerHTML = `<span class="mono-sm">${esc(pathDomain.url)}</span> ${copyButton(pathDomain.url).outerHTML} <a href="${esc(pathDomain.url)}" target="_blank" rel="noopener">open ↗</a>`;
      } else {
        epEl.textContent = "no endpoint yet — deploy the instance";
      }
      const busy = TRANSITION_STATES.has(inst.status);
      ["restart", "stop", "redeploy", "delete"].forEach((a) => {
        const btn = header.querySelector(`#a-${a}`);
        if (btn) btn.disabled = busy;
      });
      header.querySelector("#a-restart").addEventListener("click", () => action("restart"));
      header.querySelector("#a-stop").addEventListener("click", () => action("stop"));
      header.querySelector("#a-redeploy").addEventListener("click", () => action("redeploy"));
      header.querySelector("#a-delete").addEventListener("click", deleteInstance);
    }

    async function action(kind) {
      try {
        await api.post(`/api/instances/${instanceId}/${kind}`);
        toast(`Restart queued` === "" ? kind : ({ restart: "Restarting…", stop: "Stopping…", redeploy: "Redeploying…" }[kind]), "ok");
        await refresh();
      } catch (err) {
        toast(err.message, "err");
      }
    }

    async function deleteInstance() {
      if (!confirm(`Delete "${inst.name}"? This removes the instance and its endpoint permanently.`)) return;
      try {
        await api.delete(`/api/instances/${instanceId}`);
        location.hash = "#/";
      } catch (err) {
        toast(err.message, "err");
      }
    }

    function renderTabs() {
      tabsEl.innerHTML = TABS.map(
        (t) => `<button class="tab ${t === tab ? "active" : ""}" data-tab="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`
      ).join("");
      tabsEl.querySelectorAll(".tab").forEach((btn) =>
        btn.addEventListener("click", () => {
          tab = btn.dataset.tab;
          renderTabs();
          renderTab();
        })
      );
    }

    const tabRenderers = {
      overview: renderOverview,
      connections: renderConnections,
      logs: renderLogs,
      networking: renderNetworking,
      configuration: renderConfiguration,
      activity: renderActivity,
      settings: renderSettings,
    };

    function renderTab() {
      tabRenderers[tab]();
    }

    // ---- Overview --------------------------------------------------------
    let lastStatus = null;
    function renderOverview() {
      body.innerHTML = `
        <div class="kv" id="ov-kv"></div>
        <div class="card section-gap">
          <h3>Resources</h3>
          <div class="row section-gap" style="gap:24px" id="ov-meters"></div>
        </div>
        <div class="card section-gap">
          <h3>Latest deployment</h3><div id="ov-dep" class="muted">—</div>
        </div>`;
      updateOverview();
    }

    async function updateOverview() {
      const kv = body.querySelector("#ov-kv");
      if (!kv) return;
      const [metrics, status] = await Promise.all([
        api.get(`/api/instances/${instanceId}/metrics`).catch(() => ({ available: false })),
        api.get(`/api/instances/${instanceId}/status`).catch(() => ({ running: false })),
      ]);
      const latest = inst.latest_deployment;
      kv.innerHTML = `
        <div class="item"><div class="k">Status</div><div class="v">${statusEl(inst.status).innerHTML}</div></div>
        <div class="item"><div class="k">Uptime</div><div class="v">${esc(fmtUptime(status.core_health?.uptime))}</div></div>
        <div class="item"><div class="k">Connections</div><div class="v">${status.core_health?.connections ?? "—"}</div></div>
        <div class="item"><div class="k">Version</div><div class="v">${esc(status.core_health?.version || "—")}</div></div>
        <div class="item"><div class="k">Region</div><div class="v">${esc(inst.region)}</div></div>
        <div class="item"><div class="k">Health</div><div class="v" style="color:${status.healthy ? "var(--green)" : "var(--text-faint)"}">${status.healthy ? "healthy" : "n/a"}</div></div>`;
      const meters = body.querySelector("#ov-meters");
      if (meters) {
        meters.innerHTML = metrics.available
          ? meterHtml("CPU", metrics.cpu_percent, 100, "%") + meterHtml("Memory (instance)", metrics.mem_mb || 0, inst.config?.memory_mb || 256, " MB")
          : `<span class="faint">Runtime metrics unavailable (instance not running).</span>`;
      }
      const dep = body.querySelector("#ov-dep");
      if (dep && latest) {
        dep.innerHTML = `
          <div class="row">
            ${statusEl(latest.status).outerHTML}
            <span class="chip">v${latest.version}</span>
            <span class="faint">started ${fmtWhen(latest.started_at)} · took ${fmtDuration(latest.duration_ms)}</span>
          </div>
          ${latest.error ? `<p style="color:var(--red);font-size:12.5px;margin:8px 0 0">${esc(latest.error)}</p>` : ""}`;
      }
    }

    function meterHtml(label, value, max, unit) {
      const pct = Math.max(0, Math.min(100, (value / max) * 100));
      const cls = pct > 85 ? "crit" : pct > 60 ? "warn" : "";
      return `
        <div style="flex:1;min-width:220px">
          <div class="row" style="justify-content:space-between;margin-bottom:6px">
            <span class="faint" style="font-size:12px">${label}</span>
            <span class="mono-sm">${typeof value === "number" ? value.toFixed(1) : value}${unit}</span>
          </div>
          <div class="meter"><div class="${cls}" style="width:${pct}%"></div></div>
        </div>`;
    }

    // ---- Connections -----------------------------------------------------
    function renderConnections() {
      body.innerHTML = `
        <div class="row" style="margin-bottom:14px">
          <input class="input" id="conn-filter" placeholder="Filter by IP…" style="max-width:260px">
          <span class="faint" id="conn-count"></span>
        </div>
        <div class="card" style="padding:0;overflow-x:auto">
          <table class="table" id="conn-table">
            <thead><tr><th>IP</th><th>Sessions</th><th>Traffic</th><th>Transports</th><th>Last seen</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
        <p class="faint" style="font-size:12px;margin-top:10px">
          Connection metadata only. Payloads and credentials are never logged or exposed.
        </p>`;
      body.querySelector("#conn-filter").addEventListener("input", updateConnections);
      updateConnections();
    }

    async function updateConnections() {
      const tbody = body.querySelector("#conn-table tbody");
      if (!tbody) return;
      const filter = (body.querySelector("#conn-filter")?.value || "").toLowerCase();
      try {
        const data = await api.get(`/api/instances/${instanceId}/status`);
        const conns = (data.raw_connections || []).filter(
          (c) => !filter || c.ip.toLowerCase().includes(filter)
        );
        tbody.innerHTML =
          conns.length === 0
            ? `<tr><td colspan="5" class="faint" style="text-align:center;padding:24px">No active connections.</td></tr>`
            : conns
                .map(
                  (c) => `
              <tr>
                <td class="mono-sm">${esc(c.ip)}</td>
                <td>${c.sessions}</td>
                <td>${fmtBytes(c.bytes)}</td>
                <td>${(c.transports || []).map((t) => `<span class="chip">${esc(t)}</span>`).join(" ")}</td>
                <td class="faint">${fmtWhen(c.last_connected_at)}</td>
              </tr>`
                )
                .join("");
        const count = body.querySelector("#conn-count");
        if (count) count.textContent = `${conns.length} client${conns.length === 1 ? "" : "s"}`;
      } catch {
        tbody.innerHTML = `<tr><td colspan="5" class="faint" style="text-align:center;padding:24px">Instance not running.</td></tr>`;
      }
    }

    // ---- Logs ------------------------------------------------------------
    let logPaused = false;
    let logCategory = "all";
    let logBuffer = [];

    function renderLogs() {
      body.innerHTML = `
        <div class="terminal">
          <div class="terminal-toolbar">
            <select class="select" id="log-cat" style="width:130px;padding:5px 8px">
              <option value="all">All</option>
              <option value="system">System</option>
              <option value="network">Network</option>
              <option value="runtime">Runtime</option>
              <option value="error">Error</option>
            </select>
            <input class="input" id="log-search" placeholder="Search…" style="max-width:220px;padding:5px 8px">
            <div class="spacer"></div>
            <button class="btn sm" id="log-pause">Pause</button>
            <button class="btn sm" id="log-copy">Copy</button>
            <button class="btn sm" id="log-download">Download</button>
            <button class="btn sm" id="log-clear">Clear</button>
          </div>
          <div class="terminal-body" id="log-body"><div class="terminal-empty">Waiting for logs…</div></div>
        </div>`;
      body.querySelector("#log-pause").addEventListener("click", (e) => {
        logPaused = !logPaused;
        e.target.textContent = logPaused ? "Resume" : "Pause";
        e.target.classList.toggle("primary", logPaused);
      });
      body.querySelector("#log-copy").addEventListener("click", () => {
        copyText(logBuffer.map((l) => `${l.level} ${l.message}`).join("\n"));
      });
      body.querySelector("#log-download").addEventListener("click", () => {
        const blob = new Blob([logBuffer.map((l) => `${new Date(l.ts * 1000).toISOString()} ${l.level} ${l.message}`).join("\n")], { type: "text/plain" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `${inst.slug}-logs.txt`;
        a.click();
        URL.revokeObjectURL(a.href);
      });
      body.querySelector("#log-clear").addEventListener("click", () => {
        logBuffer = [];
        drawLogs();
      });
      body.querySelector("#log-search").addEventListener("input", drawLogs);
      body.querySelector("#log-cat").addEventListener("change", (e) => {
        logCategory = e.target.value;
        drawLogs();
      });
      pollLogs();
    }

    async function pollLogs() {
      if (tab !== "logs") return;
      try {
        const { logs } = await api.get(`/api/instances/${instanceId}/logs?tail=200`);
        if (!logPaused && logs.length) {
          logBuffer = logBuffer.concat(logs).slice(-800);
          drawLogs();
        }
      } catch {
        /* instance may be down */
      }
    }

    function drawLogs() {
      const el = body.querySelector("#log-body");
      if (!el) return;
      const search = (body.querySelector("#log-search")?.value || "").toLowerCase();
      const lines = logBuffer.filter(
        (l) =>
          (logCategory === "all" || l.category === logCategory) &&
          (!search || l.message.toLowerCase().includes(search))
      );
      el.innerHTML =
        lines.length === 0
          ? `<div class="terminal-empty">No matching log lines.</div>`
          : lines
              .map((l) => {
                const t = new Date(l.ts * 1000).toLocaleTimeString();
                const lvl = (l.level || "info").toLowerCase();
                return `<div class="logline ${lvl}"><span class="t">${t}</span> <span class="lv">${lvl.toUpperCase()}</span> ${esc(l.message)}</div>`;
              })
              .join("");
      el.scrollTop = el.scrollHeight;
    }

    // ---- Networking ------------------------------------------------------
    function renderNetworking() {
      const domains = inst.domains || [];
      body.innerHTML = `
        <div class="card">
          <div class="row" style="justify-content:space-between">
            <h3>Endpoints</h3>
            <button class="btn" id="dom-regen">Regenerate</button>
          </div>
          <div id="dom-list"></div>
        </div>
        <div class="card section-gap">
          <h3>Protocol behavior</h3>
          <table class="table">
            <thead><tr><th>Protocol</th><th>Path / Transport</th><th>TLS</th></tr></thead>
            <tbody>
              <tr><td>VLESS</td><td class="mono-sm">/ws/&lt;uuid&gt; (WS) · /xhttp-siz10/… (xHTTP)</td><td>automatic</td></tr>
              <tr><td>Trojan</td><td class="mono-sm">/trojan-ws (WS) · /txhttp-siz10/… (xHTTP)</td><td>automatic</td></tr>
              <tr><td>Shadowsocks</td><td class="mono-sm">/ss-ws (WS, AEAD)</td><td>automatic</td></tr>
            </tbody>
          </table>
          <p class="faint" style="font-size:12px;margin:10px 0 0">
            WebSocket upgrade, keep-alive and long-lived connections are supported end-to-end
            through the gateway. TLS is terminated at the edge.
          </p>
        </div>`;
      const list = body.querySelector("#dom-list");
      list.innerHTML =
        domains.length === 0
          ? `<p class="muted">No endpoints yet.</p>`
          : domains
              .map((d) => {
                const url = d.url || (d.kind === "path" ? `https://…/i/${d.domain}` : `https://${d.domain}`);
                return `
              <div class="endpoint" style="margin-top:10px">
                <span class="chip">${d.kind}</span>
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(url)}</span>
                ${copyButton(url).outerHTML}
                ${d.kind === "path" ? `<a href="${esc(url)}" target="_blank" rel="noopener">open ↗</a>` : ""}
              </div>`;
              })
              .join("");
      body.querySelector("#dom-regen").addEventListener("click", async () => {
        if (!confirm("Regenerate endpoints? Old links stop working immediately.")) return;
        try {
          await api.post(`/api/instances/${instanceId}/domains`);
          toast("Endpoints regenerated", "ok");
          await refresh();
        } catch (err) {
          toast(err.message, "err");
        }
      });
    }

    // ---- Configuration ---------------------------------------------------
    function renderConfiguration() {
      const c = inst.config || {};
      body.innerHTML = `
        <div class="card" style="max-width:560px">
          <h3>Instance configuration</h3>
          <p class="sub">Resources apply on the next deploy. The proxy protocol is fixed after creation.</p>
          <div class="row section-gap">
            <div class="field" style="width:170px;margin:0">
              <label>CPU (cores)</label>
              <select class="select" id="cfg-cpu">
                ${[0.25, 0.5, 1, 2, 4].map((v) => `<option value="${v}" ${c.cpu_limit === v ? "selected" : ""}>${v}</option>`).join("")}
              </select>
            </div>
            <div class="field" style="width:170px;margin:0">
              <label>Memory</label>
              <select class="select" id="cfg-mem">
                ${[128, 256, 512, 1024, 2048].map((v) => `<option value="${v}" ${c.memory_mb === v ? "selected" : ""}>${v} MB</option>`).join("")}
              </select>
            </div>
          </div>
          <div class="row section-gap">
            <button class="btn primary" id="cfg-save">Save & redeploy</button>
          </div>
        </div>`;
      body.querySelector("#cfg-save").addEventListener("click", async () => {
        const cpu = parseFloat(body.querySelector("#cfg-cpu").value);
        const mem = parseInt(body.querySelector("#cfg-mem").value, 10);
        try {
          await api.patch(`/api/instances/${instanceId}`, {
            config: { cpu_limit: cpu, memory_mb: mem },
          });
          toast("Saved — redeploying", "ok");
          await api.post(`/api/instances/${instanceId}/redeploy`);
          await refresh();
        } catch (err) {
          toast(err.message, "err");
        }
      });
    }

    // ---- Activity --------------------------------------------------------
    function renderActivity() {
      body.innerHTML = `<div class="card"><h3>Activity</h3><div class="activity-feed" id="act"></div></div>`;
      api
        .get(`/api/instances/${instanceId}/activity`)
        .then(({ activity }) => {
          const feed = body.querySelector("#act");
          feed.innerHTML =
            activity.length === 0
              ? `<div class="faint" style="padding:10px 2px">Nothing yet.</div>`
              : activity
                  .map(
                    (a) => `
                <div class="activity-item">
                  <span class="when">${fmtWhen(a.ts)}</span>
                  <span class="msg">${esc(a.message)}</span>
                </div>`
                  )
                  .join("");
        })
        .catch(() => {});
    }

    // ---- Settings --------------------------------------------------------
    function renderSettings() {
      body.innerHTML = `
        <div class="card" style="max-width:620px">
          <h3>Danger zone</h3>
          <p class="sub">These actions affect the running instance.</p>
          <div class="row section-gap">
            <button class="btn" id="s-regen-token">Rotate internal credentials</button>
            <button class="btn danger" id="s-delete">Delete instance</button>
          </div>
          <p class="faint" style="font-size:12px">
            Rotating credentials regenerates the endpoint token and the internal Core API token, then redeploys.
          </p>
        </div>`;
      body.querySelector("#s-regen-token").addEventListener("click", async () => {
        if (!confirm("Rotate credentials and redeploy? Clients must re-import the updated link.")) return;
        try {
          await api.post(`/api/instances/${instanceId}/domains`);
          await api.post(`/api/instances/${instanceId}/redeploy`);
          toast("Credentials rotated — redeploying", "ok");
          await refresh();
        } catch (err) {
          toast(err.message, "err");
        }
      });
      body.querySelector("#s-delete").addEventListener("click", deleteInstance);
    }

    // ---- refresh loop ----------------------------------------------------
    async function refresh() {
      try {
        inst = await api.get(`/api/instances/${instanceId}`);
        renderHeader();
        renderTab();
      } catch {
        /* deleted */
      }
    }

    renderHeader();
    renderTabs();
    renderTab();

    pollTimer = setInterval(() => {
      if (TRANSITION_STATES.has(inst.status) || tab === "overview" || tab === "connections") {
        refresh();
      }
      if (tab === "logs") pollLogs();
    }, 3000);

    setCleanup(() => clearInterval(pollTimer));
    return () => clearInterval(pollTimer);
  },
};
