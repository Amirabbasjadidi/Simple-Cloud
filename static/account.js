"use strict";

document.getElementById("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const current = document.getElementById("current-password");
  const next = document.getElementById("new-password");
  const repeat = document.getElementById("confirm-password");
  if (next.value !== repeat.value) {
    SC.toast("The new passwords do not match.", "error");
    repeat.focus();
    return;
  }
  try {
    await SC.api("/api/account/password", {
      method: "POST", json: { current_password: current.value, new_password: next.value },
    });
    SC.toast("Password changed.", "success");
    e.target.reset();
    if (document.querySelector(".alert-warning")) setTimeout(() => (window.location.href = "/files"), 800);
  } catch (err) {
    SC.toast(err.message, "error");
  }
});

/* Two-factor authentication */
(() => {
  const T = (s) => (SC.t ? SC.t(s) : s);
  document.getElementById("totp-start")?.addEventListener("click", async (e) => {
    try {
      const r = await SC.api("/api/account/totp/setup", { method: "POST" });
      const qr = qrcode(0, "M");  // vendored qrcode-generator
      qr.addData(r.uri);
      qr.make();
      const box = document.getElementById("totp-qr");
      box.replaceChildren(SC.el("img", { src: qr.createDataURL(5, 8), alt: "", width: 200, height: 200 }));
      document.getElementById("totp-secret").textContent = r.secret.replace(/(.{4})/g, "$1 ").trim();
      document.getElementById("totp-setup").hidden = false;
      e.target.hidden = true;
      document.getElementById("tfa-code").focus();
    } catch (err) {
      SC.toast(err.message, "error");
    }
  });
  document.getElementById("totp-enable-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await SC.api("/api/account/totp/enable", { method: "POST", json: { code: document.getElementById("tfa-code").value } });
      SC.toast(T("Two-factor authentication is on."), "success");
      setTimeout(() => location.reload(), 800);
    } catch (err) {
      SC.toast(err.message, "error");
    }
  });
  document.getElementById("totp-disable-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await SC.api("/api/account/totp/disable", { method: "POST", json: {
        password: document.getElementById("tfa-off-password").value, code: document.getElementById("tfa-off-code").value } });
      SC.toast(T("Two-factor authentication is off."), "success");
      setTimeout(() => location.reload(), 800);
    } catch (err) {
      SC.toast(err.message, "error");
    }
  });
})();
