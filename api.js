(function () {
  let csrfToken = "";

  async function refreshSession() {
    const response = await fetch("/api/auth/session", {
      headers: { Accept: "application/json" },
      credentials: "same-origin"
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not check your sign-in.");
    csrfToken = result.csrfToken;
    return result;
  }

  async function request(path, options) {
    const settings = options || {};
    const method = (settings.method || "GET").toUpperCase();
    const headers = new Headers(settings.headers || {});
    headers.set("Accept", "application/json");
    if (settings.body !== undefined) {
      headers.set("Content-Type", "application/json");
    }
    if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
      if (!csrfToken) await refreshSession();
      headers.set("X-CSRFToken", csrfToken);
    }
    const response = await fetch(path, {
      ...settings,
      method,
      headers,
      credentials: "same-origin",
      body: settings.body === undefined ? undefined : JSON.stringify(settings.body)
    });
    let result;
    try {
      result = await response.json();
    } catch {
      result = {};
    }
    if (!response.ok) {
      const error = new Error(result.error || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    if (result.csrfToken) csrfToken = result.csrfToken;
    return result;
  }

  window.siteApi = { request, refreshSession };
})();
