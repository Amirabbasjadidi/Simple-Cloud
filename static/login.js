"use strict";

/* Sign-in: password + captcha, then (if enabled) the authenticator code. */
(() => {
  const { api, toast, t } = SC;
  const form = document.getElementById("login-form");
  const totpForm = document.getElementById("totp-form");
  const img = document.getElementById("captcha-image");
  const captcha = document.getElementById("captcha");
  const password = document.getElementById("password");

  const setCaptcha = (b64) => { if (b64) { img.src = `data:image/png;base64,${b64}`; captcha.value = ""; } };

  function markInvalid(fields) {
    for (const f of form.querySelectorAll("input")) f.removeAttribute("aria-invalid");
    for (const f of fields) f.setAttribute("aria-invalid", "true");
    fields[0]?.focus();
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = document.getElementById("username");
    const empty = [username, password, captcha].filter((f) => !f.value.trim());
    if (empty.length) { markInvalid(empty); toast(t("Please fill in all fields."), "error"); return; }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const data = await api("/login", { method: "POST", json: {
        username: username.value, password: password.value, captcha: captcha.value,
        remember_me: document.getElementById("remember-me").checked,
      } });
      if (data.totp_required) {
        form.hidden = true;
        totpForm.hidden = false;
        document.getElementById("totp-code").focus();
      } else {
        window.location.href = data.redirect_url || "/files";
      }
    } catch (err) {
      setCaptcha(err.data?.captcha_image);
      markInvalid([captcha]);
      toast(err.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById("captcha-refresh").addEventListener("click", async () => {
    try { setCaptcha((await api("/refresh_captcha")).captcha_image); captcha.focus(); }
    catch (err) { toast(err.message, "error"); }
  });

  document.getElementById("show-password").addEventListener("change", (e) => {
    password.type = e.target.checked ? "text" : "password";
  });

  totpForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const data = await api("/login/totp", { method: "POST", json: { code: document.getElementById("totp-code").value } });
      window.location.href = data.redirect_url;
    } catch (err) {
      toast(err.message, "error");
      if (err.data?.restart) setTimeout(() => window.location.reload(), 1500);
    }
  });
})();
