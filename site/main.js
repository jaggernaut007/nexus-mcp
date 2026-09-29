// Copy buttons, install tabs, GitHub star count, scroll reveal.
(() => {
  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(btn.dataset.copy);
        btn.textContent = "Copied";
        btn.classList.add("done");
        setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("done"); }, 1600);
      } catch { btn.textContent = "Select + copy"; }
    });
  });

  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const select = (tab) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
    tab.focus();
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(t));
    t.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") select(tabs[(i + 1) % tabs.length]);
      if (e.key === "ArrowLeft") select(tabs[(i - 1 + tabs.length) % tabs.length]);
    });
  });

  const stars = document.querySelector("[data-stars]");
  if (stars) {
    fetch("https://api.github.com/repos/jaggernaut007/Nexus-MCP")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        // A low count reads as social anti-proof, so show it only from 10 stars.
        if (d && typeof d.stargazers_count === "number" && d.stargazers_count >= 10) {
          stars.textContent = "★ " + d.stargazers_count.toLocaleString();
          stars.hidden = false;
        }
      })
      .catch(() => {});
  }

  const year = document.querySelector("[data-year]");
  if (year) year.textContent = new Date().getFullYear();

  if ("IntersectionObserver" in window) {
    const els = document.querySelectorAll(".pain, .feature, .tool, .fact, .steps > li, .split");
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { rootMargin: "0px 0px -8% 0px" });
    els.forEach((el) => { el.classList.add("reveal"); io.observe(el); });
  }
})();
