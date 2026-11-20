"use strict";

/* URL fetch: create background jobs and poll their progress. */
(() => {
  const { api, toast, el, formatBytes } = SC;
  const T = (s) => (SC.t ? SC.t(s) : s);
  const list = document.getElementById("jobs");
  const folderSel = document.getElementById("fetch-folder");
  let timer = null;

  const STATUS = { queued: "Waiting", running: "Downloading", done: "Done", failed: "Failed", cancelled: "Cancelled" };

  api("/api/folders").then(({ folders }) => {
    const byId = new Map(folders.map((f) => [f.id, f]));
    const path = (f) => { const p = []; for (let x = f; x; x = byId.get(x.parent_id)) p.unshift(x.name); return p.join(" / "); };
    folderSel.append(...folders.map((f) => ({ value: f.id, label: path(f) }))
      .sort((a, b) => a.label.localeCompare(b.label))
      .map((o) => el("option", { value: o.value, text: o.label })));
    const pre = new URLSearchParams(location.search).get("folder");
    if (pre) folderSel.value = pre;
  }).catch(() => {});

  function jobItem(j) {
    const pct = j.total_bytes ? Math.min(100, Math.floor((j.done_bytes / j.total_bytes) * 100)) : null;
    const bar = el("div", { class: `progress ${j.status === "done" ? "progress-done" : ""} ${j.status === "failed" ? "progress-error" : ""}`,
      role: "progressbar", "aria-label": `${T("Progress")}: ${j.url}`, "aria-valuemin": 0, "aria-valuemax": 100,
      "aria-valuenow": j.status === "done" ? 100 : (pct ?? 0) },
    el("span"));
    // CSSOM, not a style attribute: the CSP blocks inline style attributes.
    bar.firstChild.style.width = `${j.status === "done" ? 100 : (pct ?? (j.status === "running" ? 5 : 0))}%`;
    let detail = T(STATUS[j.status] || j.status);
    if (j.status === "running") detail += ` · ${formatBytes(j.done_bytes)}${j.total_bytes ? ` / ${formatBytes(j.total_bytes)}` : ""}`;
    if (j.status === "done") detail += ` · ⁨${j.name}⁩`;
    if (j.error && j.status === "failed") detail += ` · ${T(j.error)}`;
    const active = j.status === "queued" || j.status === "running";
    return el("li", { class: "queue-item" },
      el("div", { class: "queue-head" },
        el("span", { class: "queue-name", dir: "ltr", title: j.url, text: j.url }),
        j.status === "done" && j.file_id ? el("a", { class: "btn btn-sm", href: `/files/${j.file_id}/download` }, SC.icon("download"), el("span", { text: T("Download") })) : null,
        el("button", { type: "button", class: "btn btn-sm", text: active ? T("Cancel") : T("Remove"),
          "aria-label": `${active ? T("Cancel") : T("Remove")}: ${j.url}`,
          onclick: () => api(`/api/fetch/${j.id}`, { method: "DELETE" }).then(refresh).catch((e) => toast(e.message, "error")) })),
      bar,
      el("span", { class: `queue-status ${j.status === "failed" ? "error" : "muted"}`, text: detail }));
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      const { jobs } = await api("/api/fetch");
      list.replaceChildren(...(jobs.length ? jobs.map(jobItem) : [el("li", { class: "queue-empty", text: T("No downloads yet.") })]));
      if (jobs.some((j) => j.status === "queued" || j.status === "running")) timer = setTimeout(refresh, 1000);
      else SC.refreshUsage();
    } catch (e) {
      list.replaceChildren(el("li", { class: "error", role: "alert", text: e.message }));
      timer = setTimeout(refresh, 5000);
    }
  }

  document.getElementById("fetch-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const urls = document.getElementById("urls").value.split(/[\s,]+/).map((u) => u.trim()).filter(Boolean);
    if (!urls.length) { toast(T("Enter at least one URL."), "error"); return; }
    try {
      await api("/api/fetch", { method: "POST", json: { urls, folder_id: folderSel.value || null } });
      document.getElementById("urls").value = "";
      toast(T("Download started."), "success");
      refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  refresh();
})();
