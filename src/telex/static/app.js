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
      const untilInput = form.querySelector('input[name="until"]');
      let end = "";
      if (untilInput && untilInput.value) {
        const day = new Date(untilInput.value + "T12:00:00");
        end = ", until " + day.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
      }
      preview.textContent = preview.dataset.prefix + describe(cadence, n, label) + end;
    };
    form.addEventListener("input", update);
    form.addEventListener("change", update);
  }

  const clock = (t) => {
    const s = Math.max(0, Math.floor(t || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };
  const saved = {
    get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (e) {} },
  };

  document.querySelectorAll("[data-played-day]").forEach((row) => {
    const at = saved.get(`radio:${row.dataset.playedDay}`);
    const label = row.querySelector("[data-played]");
    if (at === "done") label.textContent = `played · ${label.textContent}`;
    else if (at && +at > 5) label.textContent = `at ${clock(+at)} · ${label.textContent}`;
  });

  const player = document.querySelector("[data-player]");
  if (player) {
    const audio = player.querySelector("audio");
    const chapters = JSON.parse(player.dataset.chapters || "[]");
    const key = `radio:${player.dataset.day}`;
    const seek = player.querySelector("[data-seek]");
    const pos = player.querySelector("[data-pos]");
    const now = player.querySelector("[data-now]");
    const rateButton = player.querySelector("[data-rate]");
    const playIcon = player.querySelector("[data-icon-play]");
    const pauseIcon = player.querySelector("[data-icon-pause]");
    const playButton = player.querySelector("[data-play]");
    const rows = document.querySelectorAll("[data-chapter]");
    const session = "mediaSession" in navigator ? navigator.mediaSession : null;
    const rates = [1, 1.15, 1.3];
    let current = -1;
    let lastSaved = 0;
    let dragging = false;

    const chapterAt = (t) => {
      let index = 0;
      chapters.forEach((c, i) => { if (t >= c.start - 0.05) index = i; });
      return index;
    };
    const setMetadata = () => {
      if (!session || !chapters[current] || !("MediaMetadata" in window)) return;
      session.metadata = new MediaMetadata({
        title: chapters[current].title,
        artist: "Telex",
        album: player.dataset.album,
        artwork: [{ src: "/static/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
      });
    };
    const setPosition = () => {
      if (!session || !session.setPositionState || !isFinite(audio.duration)) return;
      try {
        session.setPositionState({
          duration: audio.duration,
          playbackRate: audio.playbackRate,
          position: Math.min(audio.currentTime, audio.duration),
        });
      } catch (e) {}
    };
    const update = () => {
      const t = audio.currentTime;
      if (!dragging) seek.value = t;
      pos.textContent = clock(t);
      const index = chapterAt(t);
      if (index !== current && chapters.length) {
        current = index;
        now.textContent = chapters[index].title;
        rows.forEach((row) => {
          if (+row.dataset.chapter === index) row.setAttribute("aria-current", "true");
          else row.removeAttribute("aria-current");
        });
        setMetadata();
      }
      if (Math.abs(t - lastSaved) >= 5) {
        lastSaved = t;
        saved.set(key, String(Math.floor(t)));
      }
    };
    const go = (t, play) => {
      const end = isFinite(audio.duration) ? audio.duration : t;
      audio.currentTime = Math.max(0, Math.min(t, end));
      update();
      setPosition();
      if (play) audio.play();
    };
    const previous = () => {
      const index = chapterAt(audio.currentTime);
      const start = chapters[index] ? chapters[index].start : 0;
      go(audio.currentTime - start > 3 || index === 0 ? start : chapters[index - 1].start);
    };
    const next = () => {
      const index = chapterAt(audio.currentTime);
      if (chapters[index + 1]) go(chapters[index + 1].start);
    };
    const setRate = (rate) => {
      audio.defaultPlaybackRate = rate;
      audio.playbackRate = rate;
      rateButton.textContent = `${rate}×`;
    };

    const resumeAt = +(saved.get(key) || 0) || 0;
    audio.addEventListener("loadedmetadata", () => {
      if (resumeAt > 5 && resumeAt < audio.duration - 10) go(resumeAt);
      setPosition();
    });
    audio.addEventListener("timeupdate", update);
    audio.addEventListener("ratechange", setPosition);
    audio.addEventListener("play", () => {
      playIcon.hidden = true;
      pauseIcon.hidden = false;
      playButton.setAttribute("aria-label", "Pause");
      if (session) session.playbackState = "playing";
      setMetadata();
    });
    audio.addEventListener("pause", () => {
      playIcon.hidden = false;
      pauseIcon.hidden = true;
      playButton.setAttribute("aria-label", "Play");
      saved.set(key, String(Math.floor(audio.currentTime)));
      if (session) session.playbackState = "paused";
    });
    audio.addEventListener("ended", () => saved.set(key, "done"));

    playButton.addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
    player.querySelector("[data-prev]").addEventListener("click", previous);
    player.querySelector("[data-next]").addEventListener("click", next);
    seek.addEventListener("input", () => {
      dragging = true;
      pos.textContent = clock(+seek.value);
    });
    seek.addEventListener("change", () => {
      dragging = false;
      go(+seek.value);
    });
    document.querySelectorAll("[data-seek-to]").forEach((button) => {
      button.addEventListener("click", () => go(+button.dataset.seekTo, true));
    });
    const storedRate = +saved.get("radio:rate");
    setRate(rates.includes(storedRate) ? storedRate : 1);
    rateButton.addEventListener("click", () => {
      const rate = rates[(rates.indexOf(audio.playbackRate) + 1) % rates.length];
      setRate(rate);
      saved.set("radio:rate", String(rate));
    });

    if (session) {
      const handlers = {
        play: () => audio.play(),
        pause: () => audio.pause(),
        previoustrack: previous,
        nexttrack: next,
        seekbackward: (d) => go(audio.currentTime - (d.seekOffset || 15)),
        seekforward: (d) => go(audio.currentTime + (d.seekOffset || 30)),
        seekto: (d) => go(d.seekTime),
      };
      Object.entries(handlers).forEach(([action, handler]) => {
        try { session.setActionHandler(action, handler); } catch (e) {}
      });
    }
    update();
  }
});
