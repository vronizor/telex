document.addEventListener("DOMContentLoaded", () => {
  const root = document.documentElement;
  const toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const current = root.dataset.theme
        || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = current === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (e) {}
    });
  }

  const preview = document.getElementById("slot-preview");
  if (preview) {
    const previews = JSON.parse(preview.dataset.previews);
    const update = (cadence) => {
      if (cadence === preview.dataset.originalCadence) {
        preview.textContent = "Stays " + preview.dataset.originalSchedule;
      } else {
        preview.textContent = preview.dataset.prefix + previews[cadence];
      }
    };
    document.querySelectorAll('input[name="cadence"]').forEach((input) => {
      input.addEventListener("change", () => update(input.value));
    });
  }
});
