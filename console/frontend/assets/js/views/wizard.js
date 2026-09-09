import { api, toast } from "../api.js";

const PROTOCOLS = [
  { id: "vless-ws", name: "VLESS over WebSocket", desc: "Widest client support (v2rayNG, NekoBox, Streisand). Recommended." },
  { id: "trojan-ws", name: "Trojan over WebSocket", desc: "TLS-like handshake, good with strict DPI." },
  { id: "shadowsocks", name: "Shadowsocks AEAD", desc: "Lightweight AEAD (chacha20 / aes-gcm) over WebSocket." },
  { id: "xhttp-packet-up", name: "VLESS xHTTP (packet-up)", desc: "HTTP-native transport, resists connection shaping." },
];

export default {
  async render(root) {
    const model = {
      name: "",
      region: "local",
      protocol: "vless-ws",
      cpu_limit: 0.5,
      memory_mb: 256,
    };
    const regions = await loadRegions();

    root.innerHTML = `
      <div class="wizard">
        <div class="page-head"><div><h1>Create Instance</h1>
        <div class="sub">Six steps. No servers, no Docker, no YAML.</div></div></div>
        <div class="wizard-steps" id="steps">${stepsBar(0)}</div>
        <div class="card" id="step-body"></div>
        <div class="row section-gap">
          <button class="btn ghost" id="w-back">Back</button>
          <div class="grow"></div>
          <button class="btn primary" id="w-next">Continue</button>
        </div>
      </div>`;

    let step = 0;
    const body = document.getElementById("step-body");
    const backBtn = document.getElementById("w-back");
    const nextBtn = document.getElementById("w-next");

    const stepRenderers = [renderName, renderRegion, renderConfig, renderNetworking, renderReview, renderDeploy];

    function stepsBar(current) {
      return Array.from({ length: 6 }, (_, i) =>
        `<div class="step ${i < current ? "done" : i === current ? "current" : ""}"></div>`
      ).join("");
    }

    function show() {
      document.getElementById("steps").innerHTML = stepsBar(step);
      stepRenderers[step](body);
      backBtn.disabled = step === 0;
      nextBtn.textContent = step === 4 ? "Deploy" : step === 5 ? "Go to instance" : "Continue";
      nextBtn.classList.toggle("primary", step !== 5);
      if (step === 5) nextBtn.classList.remove("primary");
    }

    async function loadRegions() {
      try {
        const { workers } = await api.get("/api/admin/workers").catch(() => ({ workers: null }));
        if (workers && workers.length) {
          return workers.filter((w) => w.enabled).map((w) => ({
            id: w.node_id,
            label: w.region,
            desc: `${w.status} · ${w.instances}/${w.capacity} instances`,
          }));
        }
      } catch {
        /* non-admin: fall through */
      }
      return [{ id: "local", label: "Local node", desc: "Default worker node" }];
    }

    async function loadRegionsSafe() { return regions; }

    function renderName(el) {
      el.innerHTML = `
        <h2>Step 1 — Name your instance</h2>
        <p class="lead">A short, unique name. It becomes part of the endpoint.</p>
        <div class="field">
          <label>Instance name</label>
          <input class="input" id="f-name" maxlength="60" placeholder="e.g. Production" value="${esc(model.name)}">
          <div class="hint">Letters, numbers and dashes. You can have up to 25 instances.</div>
        </div>`;
      el.querySelector("#f-name").addEventListener("input", (e) => (model.name = e.target.value));
    }

    async function renderRegion(el) {
      const regs = await loadRegionsSafe();
      el.innerHTML = `
        <h2>Step 2 — Deployment region</h2>
        <p class="lead">Where your instance will run. All regions behave identically for clients.</p>
        <div class="option-grid" id="region-grid">
          ${regs
            .map(
              (r) => `
            <div class="option ${model.region === r.id ? "selected" : ""}" data-id="${esc(r.id)}">
              <div class="t">${esc(r.label)}</div><div class="d">${esc(r.desc)}</div>
            </div>`
            )
            .join("")}
        </div>`;
      el.querySelectorAll(".option").forEach((opt) =>
        opt.addEventListener("click", () => {
          model.region = opt.dataset.id;
          el.querySelectorAll(".option").forEach((o) => o.classList.toggle("selected", o === opt));
        })
      );
    }

    function renderConfig(el) {
      el.innerHTML = `
        <h2>Step 3 — Configuration</h2>
        <p class="lead">Protocol and resources. You can change resources later without losing data.</p>
        <div class="field"><label>Proxy protocol</label>
          <div class="option-grid">
            ${PROTOCOLS.map(
              (p) => `
              <div class="option ${model.protocol === p.id ? "selected" : ""}" data-id="${p.id}">
                <div class="t">${p.name}</div><div class="d">${p.desc}</div>
              </div>`
            ).join("")}
          </div>
        </div>
        <div class="row" style="margin-top:16px">
          <div class="field" style="width:180px;margin:0"><label>CPU (cores)</label>
            <select class="select" id="f-cpu">
              ${[0.25, 0.5, 1, 2, 4].map((v) => `<option value="${v}" ${model.cpu_limit === v ? "selected" : ""}>${v}</option>`).join("")}
            </select></div>
          <div class="field" style="width:180px;margin:0"><label>Memory</label>
            <select class="select" id="f-mem">
              ${[128, 256, 512, 1024, 2048].map((v) => `<option value="${v}" ${model.memory_mb === v ? "selected" : ""}>${v} MB</option>`).join("")}
            </select></div>
        </div>`;
      el.querySelectorAll(".option").forEach((opt) =>
        opt.addEventListener("click", () => {
          model.protocol = opt.dataset.id;
          el.querySelectorAll(".option").forEach((o) => o.classList.toggle("selected", o === opt));
        })
      );
      el.querySelector("#f-cpu").addEventListener("change", (e) => (model.cpu_limit = parseFloat(e.target.value)));
      el.querySelector("#f-mem").addEventListener("change", (e) => (model.memory_mb = parseInt(e.target.value, 10)));
    }

    function renderNetworking(el) {
      el.innerHTML = `
        <h2>Step 4 — Networking</h2>
        <p class="lead">Every instance gets a private endpoint on this console, ready on deploy. TLS is automatic.</p>
        <div class="card" style="background:var(--bg-raised)">
          <div class="row"><span class="chip">https</span>
          <span class="mono-sm">&lt;console-host&gt;/i/&lt;private-token&gt;</span></div>
          <p class="faint" style="margin:10px 0 0;font-size:12.5px">
            WebSocket, xHTTP and all Lunel protocols work through this endpoint with automatic TLS.
            A provider hostname (e.g. <span class="mono-sm">name-xxxx.lunel.app</span>) is attached
            automatically when the platform supports it.
          </p>
        </div>`;
    }

    function renderReview(el) {
      el.innerHTML = `
        <h2>Step 5 — Review</h2>
        <p class="lead">Confirm the configuration, then deploy.</p>
        <table class="review-table">
          <tr><td>Name</td><td>${esc(model.name) || "—"}</td></tr>
          <tr><td>Region</td><td>${esc(model.region)}</td></tr>
          <tr><td>Protocol</td><td>${esc(model.protocol)}</td></tr>
          <tr><td>CPU / Memory</td><td>${model.cpu_limit} core / ${model.memory_mb} MB</td></tr>
          <tr><td>Endpoint</td><td>https://…/i/&lt;token&gt; (generated)</td></tr>
        </table>`;
    }

    function renderDeploy(el) {
      el.innerHTML = `
        <h2>Step 6 — Deploy</h2>
        <div class="kv" style="margin-top:14px">
          <div class="item"><div class="k">Status</div><div class="v" id="dep-status">Deploying…</div></div>
          <div class="item"><div class="k">Deployment</div><div class="v" id="dep-id">—</div></div>
        </div>
        <div class="terminal section-gap"><div class="terminal-body" id="dep-log">
          <div class="logline"><span class="t">»</span> queued</div>
        </div></div>`;
    }

    async function startDeploy(el) {
      const created = await api.post("/api/instances", {
        name: model.name,
        region: model.region,
        config: {
          protocol: model.protocol,
          cpu_limit: model.cpu_limit,
          memory_mb: model.memory_mb,
        },
      });
      const depId = await api.post(`/api/instances/${created.id}/deploy`);
      el.querySelector("#dep-id").textContent = depId.deployment_id.slice(0, 8);
      await pollDeployment(el, created.id, depId.deployment_id);
      return created;
    }

    async function pollDeployment(el, instanceId, deploymentId) {
      const statusEl = el.querySelector("#dep-status");
      const logEl = el.querySelector("#dep-log");
      let seen = 0;
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        try {
          const { logs } = await api.get(`/api/instances/${instanceId}/deployments/${deploymentId}/logs`);
          for (; seen < logs.length; seen++) {
            const line = document.createElement("div");
            line.className = `logline ${logs[seen].level}`;
            line.innerHTML = `<span class="lv">${logs[seen].level}</span> ${esc(logs[seen].message)}`;
            logEl.appendChild(line);
            logEl.scrollTop = logEl.scrollHeight;
          }
          const { deployments } = await api.get(`/api/instances/${instanceId}/deployments`);
          const dep = deployments.find((d) => d.id === deploymentId);
          if (dep) {
            statusEl.textContent = dep.status.replace("_", " ");
            if (dep.status === "running") {
              statusEl.style.color = "var(--green)";
              toast("Instance is running", "ok");
              return;
            }
            if (dep.status === "failed") {
              statusEl.style.color = "var(--red)";
              toast(`Deployment failed: ${dep.error || "unknown"}`, "err", 8000);
              return;
            }
          }
        } catch {
          /* transient */
        }
      }
      statusEl.textContent = "still deploying…";
    }

    nextBtn.addEventListener("click", async () => {
      if (step === 0) {
        if (model.name.trim().length < 2) {
          toast("Give your instance a name (2+ characters)", "err");
          return;
        }
        step = 1;
      } else if (step === 4) {
        step = 5;
        show();
        nextBtn.disabled = true;
        try {
          const created = await startDeploy(body);
          nextBtn.textContent = "Go to instance";
          nextBtn.disabled = false;
          nextBtn.classList.add("primary");
          nextBtn.onclick = () => (location.hash = `#/instances/${created.id}`);
        } catch (err) {
          toast(err.message, "err", 6000);
          step = 4;
          show();
          nextBtn.disabled = false;
        }
        return;
      } else if (step < 5) {
        step += 1;
      }
      show();
    });

    backBtn.addEventListener("click", () => {
      if (step > 0 && step !== 5) {
        step -= 1;
        show();
      }
    });

    show();
  },
};
