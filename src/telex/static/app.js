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

  document.querySelectorAll("[data-copy]").forEach((button) => {
    const label = button.textContent;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        button.textContent = "Copied — paste it into Claude";
      } catch (e) {
        button.textContent = "Copy failed";
      }
      setTimeout(() => { button.textContent = label; }, 2500);
    });
  });

  const preview = document.getElementById("slot-preview");
  if (preview) {
    const form = preview.closest("form");
    const slots = JSON.parse(preview.dataset.slots);
    const unitOf = { daily: "day", weekly: "week", monthly: "month" };
    const describe = (cadence, n, label) => {
      const base = n === 1 ? `every ${unitOf[cadence]}` : `every ${n} ${unitOf[cadence]}s`;
      if (cadence === "daily") return base;
      if (!label) return `${base}, day not set yet`;
      if (cadence === "weekly") return n === 1 ? `every ${label}` : `${base} on ${label}`;
      return n === 1 ? `monthly on the ${label}` : `${base} on the ${label}`;
    };
    const update = () => {
      const checked = form.querySelector('input[name="cadence"]:checked');
      const n = Math.max(1, parseInt(form.querySelector('input[name="every"]').value, 10) || 1);
      form.querySelectorAll("[data-unit]").forEach((el) => {
        el.textContent = el.dataset.unit + (n === 1 ? "" : "s");
      });
      if (!checked) return;
      const cadence = checked.value;
      const label = cadence === preview.dataset.originalCadence ? preview.dataset.originalSlot : slots[cadence];
      preview.textContent = preview.dataset.prefix + describe(cadence, n, label);
    };
    form.addEventListener("input", update);
    form.addEventListener("change", update);
  }
});
