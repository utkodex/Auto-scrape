// Shared helpers for the SuccessFactors-like fixture pages.
// The query string is propagated to every page so test variants
// (?popup=1, ?login=1, ?many=1, ?id=N) survive navigation.
(function () {
  const q = location.search || "";
  const params = new URLSearchParams(q);
  window.SF = {
    q: q,
    flag: function (name) { return params.get(name) === "1"; },
    hasSession: function () { return document.cookie.split(";").some(c => c.trim() === "sf_session=1"); },
    go: function (page) { location.href = page + q; },
    requireLogin: function () {
      if (window.SF.flag("login") && !window.SF.hasSession()) {
        location.replace("login.html" + q);
        return true;
      }
      return false;
    },
    validate: function (form) {
      const missing = [];
      form.querySelectorAll("[required]").forEach(el => {
        if (el.type === "radio") {
          const group = form.querySelectorAll('input[name="' + el.name + '"]');
          if (![...group].some(r => r.checked)) missing.push(el.name);
        } else if (el.type === "file") {
          if (!el.files || el.files.length === 0) missing.push(el.id);
        } else if (!el.value) {
          missing.push(el.id);
        }
      });
      const unique = [...new Set(missing)];
      const err = document.getElementById("error");
      if (unique.length) {
        err.textContent = "Please complete required fields: " + unique.join(", ");
        err.hidden = false;
        return false;
      }
      err.hidden = true;
      return true;
    }
  };
})();
