"use strict";

(() => {
  const { api, toast, el, formatBytes } = SC;
  const tbody = document.getElementById("users-body");
  const T = (s) => (SC.t ? SC.t(s) : s);

  async function load() {
    try {
      const { users } = await api("/api/admin/users");
      tbody.replaceChildren(...users.map(row));
    } catch (e) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: 6, class: "error", text: e.message })));
    }
  }

  async function patch(u, body, okMessage) {
    try {
      const r = await api(`/api/admin/users/${u.id}`, { method: "PATCH", json: body });
      if (r.temporary_password) {
        await SC.confirm(`${T("Temporary password for")} ${u.username}: ${r.temporary_password}`,
          { title: T("Password reset"), confirmText: T("Done") });
      } else if (okMessage) {
        toast(okMessage, "success");
      }
      load();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  function row(u) {
    const quota = el("input", {
      type: "number", min: 0, class: "input-sm", value: u.quota_bytes ? Math.round(u.quota_bytes / 1048576) : "",
      "aria-label": `${T("Quota (MB) for")} ${u.username}`, placeholder: "∞",
    });
    quota.addEventListener("change", () => patch(u, { quota_mb: quota.value || null }, T("Quota updated.")));
    const actions = el("div", { class: "row-actions" },
      el("button", { class: "btn btn-sm", type: "button", disabled: u.is_self,
        onclick: () => patch(u, { disabled: !u.disabled }, u.disabled ? T("User enabled.") : T("User disabled.")),
        text: u.disabled ? T("Enable") : T("Disable") }),
      el("button", { class: "btn btn-sm", type: "button", disabled: u.is_self,
        onclick: () => patch(u, { is_admin: !u.is_admin }, T("Role updated.")),
        text: u.is_admin ? T("Remove admin") : T("Make admin") }),
      el("button", { class: "btn btn-sm", type: "button",
        onclick: async () => {
          if (await SC.confirm(`${T("Reset the password of")} ${u.username}?`, { confirmText: T("Reset") }))
            patch(u, { reset_password: true });
        }, text: T("Reset password") }),
      u.totp_enabled ? el("button", { class: "btn btn-sm", type: "button",
        onclick: async () => {
          if (await SC.confirm(`${T("Turn off two-factor authentication for")} ${u.username}?`, { confirmText: T("Turn off") }))
            patch(u, { reset_2fa: true }, T("Two-factor authentication turned off."));
        }, text: T("Reset 2FA") }) : null,
      el("button", { class: "btn btn-sm btn-danger", type: "button", disabled: u.is_self,
        onclick: () => remove(u), text: T("Delete") }),
    );
    const usage = `${formatBytes(u.used_bytes)} · ${SC.num(u.file_count)} ${T("files")}`;
    const status = u.disabled ? ["Disabled", "badge-danger"]
      : (u.must_change_password ? ["Must change password", "badge-warning"] : ["Active", "badge-success"]);
    return el("tr", {},
      el("th", { scope: "row" }, el("span", { dir: "auto", text: u.username }), u.is_self ? el("span", { class: "muted", text: ` (${T("you")})` }) : null),
      el("td", { "data-label": T("Role") }, el("span", { class: `badge ${u.is_admin ? "badge-primary" : ""}`, text: u.is_admin ? T("Admin") : T("User") })),
      el("td", { "data-label": T("Status") }, el("span", { class: `badge ${status[1]}`, text: T(status[0]) })),
      el("td", { "data-label": T("Usage"), text: usage }),
      el("td", { "data-label": T("Quota (MB)") }, quota),
      el("td", {}, actions));
  }

  function remove(u) {
    const dlg = document.getElementById("delete-dialog");
    document.getElementById("delete-summary").textContent =
      `${u.username}: ${SC.num(u.file_count)} ${T("files")}${SC.lang === "fa" ? "،" : ","} ${formatBytes(u.used_bytes)}. ${T("This cannot be undone.")}`;
    document.getElementById("delete-files").hidden = u.file_count === 0;
    SC.openDialog(dlg).then(async (ok) => {
      if (!ok) return;
      const files = dlg.querySelector('input[name="files"]:checked').value;
      try {
        await api(`/api/admin/users/${u.id}`, {
          method: "DELETE", json: { files, to: document.getElementById("delete-to").value.trim() },
        });
        toast(T("User deleted."), "success");
        load();
      } catch (e) {
        toast(e.message, "error");
      }
    });
  }

  document.getElementById("create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/admin/users", { method: "POST", json: {
        username: document.getElementById("new-username").value.trim(),
        password: document.getElementById("new-password").value,
        quota_mb: document.getElementById("new-quota").value || null,
        is_admin: document.getElementById("new-admin").checked,
      } });
      toast(T("User created. They must change the password at first login."), "success");
      e.target.reset();
      load();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  load();
})();

/* System / storage dashboard */
(() => {
  const root = document.getElementById("system");
  if (!root) return;
  const { api, toast, el, formatBytes, num } = SC;
  const T = SC.t;
  const $ = (id) => document.getElementById(id);
  const when = (sec) => new Date(sec * 1000).toLocaleString(SC.lang);
  const pct = (v) => (v === null || v === undefined ? "—" : `${num(v, 1)}%`);
  let users = [];
  let sort = { key: "used", dir: "descending" };
  let pollTimer = null;

  const WARNINGS = {
    over_allocated: (w) => T("{allocated} is allocated in quotas, but only {available} is available (free space plus current cloud data). If users fill their quotas, the disk will run out.",
      { allocated: formatBytes(w.allocated), available: formatBytes(w.available) }),
    unlimited_users: (w) => T("Users with an unlimited quota can fill the whole disk: {users}.", { users: w.users.join(SC.lang === "fa" ? "، " : ", ") }),
    low_disk: (w) => T("Only {free} ({pct}) is free on the upload disk (warning below {threshold}%).",
      { free: formatBytes(w.free), pct: pct(w.free_pct), threshold: num(w.threshold) }),
    backup_missing: () => T("No database backup was found in the backup folder."),
    backup_old: (w) => T("The newest database backup is {age} hours old (more than {limit} hours).",
      { age: num(w.age_hours, 1), limit: num(w.limit) }),
    backup_not_writable: (w) => T("The backup folder {folder} is not writable by the Simple Cloud service. Re-run the installer or fix the folder's permissions.", { folder: w.folder }),
    backup_timer_off: () => T("The nightly database backup timer is off."),
  };

  function renderWarnings(list) {
    $("system-warnings").replaceChildren(...list.map((w) => el("div", { class: `alert ${w.code === "over_allocated" || w.code === "backup_not_writable" || w.code === "low_disk" ? "alert-error" : ""}`, role: "alert" },
      SC.icon("alert"), el("span", { text: (WARNINGS[w.code] || (() => w.code))(w) }))));
  }

  function renderDisk(d) {
    const segs = $("disk-bar").children;
    [d.cloud, d.other, d.free].forEach((v, i) => { segs[i].style.inlineSize = `${d.total ? (100 * v) / d.total : 0}%`; });
    $("disk-bar").setAttribute("aria-label", `${T("Disk usage")}: ${T("Simple Cloud files")} ${formatBytes(d.cloud)}, ${T("Other data on this disk")} ${formatBytes(d.other)}, ${T("Free")} ${formatBytes(d.free)}`);
    $("disk-cloud").textContent = formatBytes(d.cloud);
    $("disk-other").textContent = formatBytes(d.other);
    $("disk-free").textContent = `${formatBytes(d.free)} (${pct(d.free_pct)})`;
    $("disk-total").textContent = T("{used} of {total} used", { used: formatBytes(d.used), total: formatBytes(d.total) });
  }

  function renderAllocation(a) {
    $("alloc-quota").textContent = formatBytes(a.quota_total);
    $("alloc-capacity").textContent = formatBytes(a.capacity);
    $("alloc-available").textContent = formatBytes(a.available);
    $("alloc-unlimited").textContent = a.unlimited_users.length ? a.unlimited_users.join(SC.lang === "fa" ? "، " : ", ") : T("None");
    $("alloc-quota").classList.toggle("error", a.over_available);
  }

  function renderUsers() {
    const k = sort.key;
    const sign = sort.dir === "ascending" ? 1 : -1;
    const rows = [...users].sort((x, y) => {
      const a = x[k] ?? (k === "quota" ? Infinity : -1);
      const b = y[k] ?? (k === "quota" ? Infinity : -1);
      return sign * (typeof a === "string" ? a.localeCompare(b) : a - b);
    });
    root.querySelectorAll("#usage-table th[data-sort]").forEach((th) =>
      (th.dataset.sort === k ? th.setAttribute("aria-sort", sort.dir) : th.removeAttribute("aria-sort")));
    $("usage-body").replaceChildren(...rows.map((u) => el("tr", {},
      el("th", { scope: "row" }, el("span", { dir: "auto", text: u.username }),
        u.disabled ? el("span", { class: "badge badge-danger", text: T("Disabled") }) : null),
      el("td", { class: "num", "data-label": T("Used"), text: `${formatBytes(u.used)} · ${num(u.file_count)} ${T("files")}` }),
      el("td", { class: "num", "data-label": T("Quota"), text: u.quota ? formatBytes(u.quota) : T("Unlimited") }),
      el("td", { class: "num", "data-label": T("% of quota"), text: pct(u.pct_quota) }),
      el("td", { class: "num", "data-label": T("% of disk"), text: pct(u.pct_disk) }),
      el("td", { class: "num", "data-label": T("Share of cloud data"), text: pct(u.pct_cloud) }))));
  }

  root.querySelectorAll("#usage-table th[data-sort] .sort").forEach((b) => b.addEventListener("click", () => {
    const key = b.closest("th").dataset.sort;
    sort = { key, dir: sort.key === key && sort.dir === "descending" ? "ascending" : (key === "username" && sort.key !== key ? "ascending" : "descending") };
    renderUsers();
  }));

  function renderLargest(list) {
    $("largest-body").replaceChildren(...(list.length ? list.map((f) => el("tr", {},
      el("th", { scope: "row" }, el("span", { dir: "auto", text: f.name })),
      el("td", { "data-label": T("Owner"), dir: "auto", text: f.owner }),
      el("td", { class: "num", "data-label": T("Size"), text: formatBytes(f.size) }),
      el("td", { "data-label": T("Uploaded"), text: SC.formatDate(f.created_at) })))
      : [el("tr", {}, el("td", { colspan: 4, class: "muted", text: T("No files yet.") }))]));
  }

  function renderBackups(b) {
    $("bk-last").textContent = b.last
      ? `${when(b.last)} (${T("{age} hours ago", { age: num(b.age_hours, 1) })})` : T("Never");
    $("bk-timer").textContent = b.timer === null ? T("Unknown (not a systemd install)") : (b.timer ? T("On (daily at 03:00)") : T("Off"));
    $("bk-folder").textContent = b.folder;
    $("bk-keep").textContent = T("Keeps the newest {n}", { n: num(b.keep) });
    $("bk-count").textContent = T("{n} database backups", { n: num(b.count) });
    $("bk-body").replaceChildren(...b.archives.map((a) => el("tr", {},
      el("td", { dir: "ltr", text: a.name }), el("td", { text: when(a.mtime) }), el("td", { class: "num", text: formatBytes(a.size) }))));
    $("backup-now").disabled = !b.writable;
  }

  function renderRun(run) {
    const busy = run && run.status === "running";
    $("backup-progress").hidden = !busy;
    $("backup-now").setAttribute("aria-busy", String(busy));
    if (busy) $("backup-now").disabled = true;
    if (!run) { $("backup-run").textContent = ""; return busy; }
    const name = run.path ? run.path.split(/[\\/]/).pop() : "";
    $("backup-run").textContent = busy ? T("Backing up…")
      : run.status === "done" ? `${T("Last manual backup finished")}: ${name} · ${when(run.finished_at)}`
        : `${T("Backup failed")}: ${T(run.error || "")}`;
    $("backup-run").className = run.status === "failed" ? "error" : "muted";
    return busy;
  }

  async function load() {
    root.setAttribute("aria-busy", "true");
    try {
      const d = await api("/api/admin/system");
      users = d.users;
      renderWarnings([...d.warnings, ...d.backups.warnings]);
      renderDisk(d.disk);
      renderAllocation(d.allocation);
      renderUsers();
      renderLargest(d.largest);
      renderBackups(d.backups);
      if (renderRun(d.backup_run)) poll();
      $("system-updated").textContent = `${T("Updated")} ${when(d.generated_at)} · ${T("Figures are refreshed at most once a minute.")}`;
    } catch (e) {
      $("system-warnings").replaceChildren(el("div", { class: "alert alert-error", role: "alert" }, SC.icon("alert"), el("span", { text: e.message })));
    } finally {
      root.setAttribute("aria-busy", "false");
    }
  }

  async function poll() {
    clearTimeout(pollTimer);
    try {
      const { run } = await api("/api/admin/backup");
      if (renderRun(run)) { pollTimer = setTimeout(poll, 1000); return; }
      toast(run.status === "done" ? T("Database backup finished.") : `${T("Backup failed")}: ${T(run.error || "")}`,
        run.status === "done" ? "success" : "error");
      load();
    } catch (e) {
      pollTimer = setTimeout(poll, 3000);
    }
  }

  $("backup-now").addEventListener("click", async () => {
    $("backup-now").disabled = true;
    try {
      const { run } = await api("/api/admin/backup", { method: "POST" });
      renderRun(run);
      poll();
    } catch (e) {
      toast(e.message, "error");
      if (e.data?.run) { renderRun(e.data.run); poll(); } else $("backup-now").disabled = false;
    }
  });

  load();
})();
