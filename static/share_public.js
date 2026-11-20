"use strict";

(() => {
  const meta = document.getElementById("share-meta");
  if (meta) {
    document.getElementById("share-size").textContent = SC.formatBytes(Number(meta.dataset.size));
    const exp = document.getElementById("share-expires");
    if (exp && meta.dataset.expires) {
      const d = new Date(Number(meta.dataset.expires) * 1000);
      exp.dateTime = d.toISOString();
      exp.textContent = d.toLocaleString(document.documentElement.lang || undefined);
    }
  }
  const form = document.getElementById("unlock-form");
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await SC.api(`/s/${encodeURIComponent(form.dataset.token)}`, {
        method: "POST", json: { password: document.getElementById("share-password").value },
      });
      location.reload();
    } catch (err) {
      SC.toast(err.message, "error");
      document.getElementById("share-password").select();
    }
  });
})();
