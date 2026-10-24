(function () {
  const storageKey = "between-the-lines-theme";
  const root = document.documentElement;

  function applyTheme(theme) {
    const dark = theme === "dark";
    root.dataset.theme = dark ? "dark" : "light";
    const button = document.querySelector(".theme-toggle");
    if (button) {
      button.textContent = dark ? "☀ Light theme" : "☾ Dark theme";
      button.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
      button.setAttribute("aria-pressed", String(dark));
    }
  }

  let savedTheme = "light";
  try {
    savedTheme = localStorage.getItem(storageKey) === "dark" ? "dark" : "light";
  } catch (error) {
    console.warn("Theme preference could not be read.", error);
  }
  applyTheme(savedTheme);

  function addThemeToggle() {
    const button = document.createElement("button");
    button.className = "theme-toggle";
    button.type = "button";
    button.addEventListener("click", function () {
      const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(nextTheme);
      try {
        localStorage.setItem(storageKey, nextTheme);
      } catch (error) {
        console.warn("Theme preference could not be saved.", error);
      }
    });
    document.body.append(button);
    applyTheme(savedTheme);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", addThemeToggle, { once: true });
  } else {
    addThemeToggle();
  }
})();
