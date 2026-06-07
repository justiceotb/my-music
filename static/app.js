/* app.js - Music collection UI */

const state = {
  q: "",
  albumId: null,
  tag: null,
  filter: null,
  page: 1,
  trackSort: "artist",
  albumSort: "artist",
  themeFilter: null,
};

let jobPollTimer = null;
let currentJobId = null;

// ── Utility ──────────────────────────────────

function el(id) { return document.getElementById(id); }

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Stats ─────────────────────────────────────

async function loadStats() {
  try {
    const s = await apiFetch("/api/stats");
    el("stats-display").textContent =
      `${s.albums} albums · ${s.tracks} tracks · ${s.lyrics_found} lyrics · ${s.summarised} summarised`;
  } catch (_) {}
}

// ── Albums sidebar ────────────────────────────

async function loadAlbums() {
  const albums = await apiFetch(`/api/albums?sort=${state.albumSort}`);
  el("album-count").textContent = `(${albums.length})`;
  const ul = el("albums");
  ul.innerHTML = '<li data-id="" class="active">All albums</li>';
  albums.forEach(a => {
    const li = document.createElement("li");
    li.dataset.id = a.discogs_id;
    const artist = a.artists_sort ? `${a.artists_sort} - ` : "";
    li.textContent = `${artist}${a.title} (${a.year || "?"})`;
    ul.appendChild(li);
  });
  ul.addEventListener("click", e => {
    const li = e.target.closest("li");
    if (!li) return;
    ul.querySelectorAll("li").forEach(x => x.classList.remove("active"));
    li.classList.add("active");
    state.albumId = li.dataset.id ? Number(li.dataset.id) : null;
    state.page = 1;
    updateResetBtn();
    loadTracks();
  });
}

// ── Tag cloud ─────────────────────────────────

let allTags = [];
let tagsSort = "count-desc";

function renderTags() {
  const container = el("tags");
  container.innerHTML = "";

  const sorted = [...allTags].sort((a, b) => {
    if (tagsSort === "count-asc")  return a.count - b.count;
    if (tagsSort === "alpha")      return a.tag.localeCompare(b.tag);
    return b.count - a.count; // count-desc default
  });

  sorted.forEach(({ tag, count }) => {
    const span = document.createElement("span");
    span.className = "tag-pill";
    if (state.tag === tag) span.classList.add("active");
    span.textContent = `${tag} (${count})`;
    span.addEventListener("click", () => {
      if (state.tag === tag) {
        state.tag = null;
        span.classList.remove("active");
      } else {
        container.querySelectorAll(".tag-pill").forEach(x => x.classList.remove("active"));
        state.tag = tag;
        span.classList.add("active");
      }
      state.page = 1;
      updateResetBtn();
      loadTracks();
    });
    container.appendChild(span);
  });
}

async function loadTags() {
  try {
    allTags = await apiFetch("/api/tags");
    const total = el("tags-total");
    if (total) total.textContent = `${allTags.length} unique tags`;

    document.querySelectorAll(".tags-sort").forEach(btn => {
      btn.addEventListener("click", () => {
        tagsSort = btn.dataset.sort;
        document.querySelectorAll(".tags-sort").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        renderTags();
      });
    });

    renderTags();
  } catch (_) {}
}

async function loadThemes() {
  try {
    const themes = await apiFetch("/api/themes");
    if (!themes.length) return;

    const sel = el("theme-filter");
    themes.forEach(({ theme, tag_count }) => {
      const opt = document.createElement("option");
      opt.value = theme;
      opt.textContent = `${theme} (${tag_count})`;
      sel.appendChild(opt);
    });
    el("theme-filter-row").classList.remove("hidden");

    sel.addEventListener("change", async () => {
      state.themeFilter = sel.value || null;
      const url = state.themeFilter
        ? `/api/tags?theme=${encodeURIComponent(state.themeFilter)}`
        : "/api/tags";
      try {
        allTags = await apiFetch(url);
        const total = el("tags-total");
        if (total) total.textContent = `${allTags.length} unique tags`;
        renderTags();
      } catch (_) {}
    });
  } catch (_) {}
}

// ── Tracks ────────────────────────────────────

async function loadTracks() {
  const params = new URLSearchParams({ page: state.page, sort: state.trackSort });
  if (state.q)       params.set("q", state.q);
  if (state.albumId) params.set("album_id", state.albumId);
  if (state.tag)     params.set("tag", state.tag);
  if (state.filter)  params.set("filter", state.filter);

  const data = await apiFetch(`/api/tracks?${params}`);
  renderTracks(data);
}

function renderTracks({ tracks, total, page, per_page }) {
  const pages = Math.ceil(total / per_page);
  el("result-info").textContent = pages > 1
    ? `${total} tracks found — page ${page} of ${pages}`
    : `${total} tracks found`;

  const container = el("tracks");
  container.innerHTML = "";

  if (!tracks.length) {
    container.innerHTML = "<p>No tracks found.</p>";
    el("pagination").innerHTML = "";
    return;
  }

  tracks.forEach(t => {
    const card = document.createElement("article");
    card.className = "track-card";
    card.dataset.id = t.id;

    const tagsHtml = t.theme_tags
      ? JSON.parse(t.theme_tags).map(tag =>
          `<span class="tag-pill" data-tag="${tag}">${tag}</span>`
        ).join("")
      : "";

    const lyricsBadge = t.lyrics_source && !["not_found", "error"].includes(t.lyrics_source)
      ? '<span class="badge badge-found">lyrics</span>'
      : (t.lyrics_source === "not_found" || t.lyrics_source === "error")
        ? '<span class="badge badge-missing">no lyrics</span>'
        : "";

    const aiBadge = t.ai_processed_at
      ? '<span class="badge badge-ai">summarised</span>'
      : "";

    const tagsBadge = t.theme_tags && JSON.parse(t.theme_tags).length
      ? '<span class="badge badge-tags">tags</span>'
      : "";

    const trackArtist = t.artists || t.artists_sort || "";
    card.innerHTML = `
      <h4>${escHtml(t.title)}</h4>
      <div class="track-meta">
        ${trackArtist ? escHtml(trackArtist) + ' &mdash; ' : ''}${escHtml(t.album)} (${t.year || "?"})
        ${t.position ? `&middot; ${escHtml(t.position)}` : ""}
      </div>
      <div class="track-chips">${lyricsBadge}${aiBadge}${tagsBadge}</div>
      ${t.summary ? `<p class="track-summary">${escHtml(t.summary.slice(0, 200))}…</p>` : ""}
      <div class="track-tags">${tagsHtml}</div>
    `;

    // Tag pill clicks within card
    card.querySelectorAll(".tag-pill[data-tag]").forEach(pill => {
      pill.addEventListener("click", e => {
        e.stopPropagation();
        state.tag = pill.dataset.tag;
        state.page = 1;
        document.querySelectorAll("#tags .tag-pill").forEach(x => {
          x.classList.toggle("active", x.textContent.startsWith(state.tag));
        });
        updateResetBtn();
        loadTracks();
      });
    });

    card.addEventListener("click", () => openModal(t.id));
    container.appendChild(card);
  });

  renderPagination(total, page, per_page);
}

function renderPagination(total, page, per_page) {
  const pages = Math.ceil(total / per_page);
  const pag = el("pagination");
  pag.innerHTML = "";
  if (pages <= 1) return;

  const mkBtn = (label, p, disabled = false) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.className = "outline secondary";
    btn.disabled = disabled;
    btn.addEventListener("click", () => { state.page = p; loadTracks(); });
    return btn;
  };

  pag.appendChild(mkBtn("« First", 1, page === 1));
  if (pages > 10 && page > 10) pag.appendChild(mkBtn("−10", page - 10));
  pag.appendChild(mkBtn("‹ Prev", page - 1, page === 1));

  const start = Math.max(1, page - 2);
  const end   = Math.min(pages, page + 2);
  for (let p = start; p <= end; p++) {
    const btn = mkBtn(p, p, p === page);
    if (p === page) btn.classList.add("active");
    pag.appendChild(btn);
  }

  pag.appendChild(mkBtn("Next ›", page + 1, page === pages));
  if (pages > 10 && page <= pages - 10) pag.appendChild(mkBtn("+10", page + 10));
  pag.appendChild(mkBtn("Last »", pages, page === pages));
}

// ── Modal ─────────────────────────────────────

let currentModalTrackId = null;

async function openModal(trackId) {
  currentModalTrackId = trackId;
  const t = await apiFetch(`/api/track/${trackId}`);
  el("modal-title").textContent = t.title;
  el("modal-meta").textContent =
    `${t.artists_sort || t.artists || ""} - ${t.album} (${t.year || "?"})`;

  const link = el("modal-discogs-link");
  if (t.album_id) {
    link.href = `https://www.discogs.com/release/${t.album_id}`;
    link.classList.remove("hidden");
  } else {
    link.classList.add("hidden");
  }

  const tagsArr = t.theme_tags ? JSON.parse(t.theme_tags) : [];
  el("modal-tags").innerHTML = tagsArr
    .map(tag => `<span class="tag-pill">${escHtml(tag)}</span>`)
    .join("");

  el("modal-summary").textContent = t.summary || "(No summary yet)";
  const casualEl = el("modal-casual");
  if (t.summary_casual) {
    casualEl.textContent = `"${t.summary_casual}"`;
    casualEl.style.display = "";
  } else {
    casualEl.style.display = "none";
  }
  el("modal-lyrics").textContent = t.lyrics || "(No lyrics yet)";
  el("track-modal").showModal();
}

el("modal-close").addEventListener("click", () => el("track-modal").close());
el("track-modal").addEventListener("click", e => {
  if (e.target === el("track-modal")) el("track-modal").close();
});

el("modal-btn-lyrics").addEventListener("click", () => {
  if (!currentModalTrackId) return;
  el("track-modal").close();
  const jobId = `fetch_lyrics_${currentModalTrackId}`;
  startJob(jobId, () => apiFetch(`/api/fetch-lyrics/${currentModalTrackId}`, { method: "POST" }));
});

el("modal-btn-summarise-ollama").addEventListener("click", () => {
  if (!currentModalTrackId) return;
  el("track-modal").close();
  const jobId = `summarise_${currentModalTrackId}`;
  startJob(jobId, () => apiFetch(`/api/summarise/${currentModalTrackId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_type: "ollama" }),
  }));
});


// ── Search ────────────────────────────────────

el("search").addEventListener("input", debounce(e => {
  state.q = e.target.value.trim();
  state.page = 1;
  loadTracks();
}, 300));

// ── Sidebar tabs ──────────────────────────────

document.querySelectorAll(".sidebar-tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll(".sidebar-tab-btn").forEach(b => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".sidebar-tab-panel").forEach(p => p.classList.toggle("hidden", p.dataset.panel !== tab));
  });
});

// ── Filter chips ─────────────────────────────

function updateResetBtn() {
  const active = state.filter || state.tag || state.albumId;
  el("btn-reset-filters").classList.toggle("hidden", !active);
}

document.querySelectorAll(".filter-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    const val = chip.dataset.filter;
    if (state.filter === val) {
      state.filter = null;
      chip.classList.remove("active");
    } else {
      document.querySelectorAll(".filter-chip").forEach(c => c.classList.remove("active"));
      state.filter = val;
      chip.classList.add("active");
    }
    state.page = 1;
    updateResetBtn();
    loadTracks();
  });
});

el("btn-reset-filters").addEventListener("click", () => {
  state.filter = null;
  state.tag = null;
  state.albumId = null;
  state.page = 1;
  document.querySelectorAll(".filter-chip").forEach(c => c.classList.remove("active"));
  document.querySelectorAll("#tags .tag-pill").forEach(x => x.classList.remove("active"));
  document.querySelectorAll("#albums li").forEach(li => li.classList.toggle("active", li.dataset.id === ""));
  updateResetBtn();
  loadTracks();
});

// ── Sort ──────────────────────────────────────

el("sort-select").addEventListener("change", e => {
  state.trackSort = e.target.value;
  state.page = 1;
  loadTracks();
});

el("album-sort-select").addEventListener("change", e => {
  state.albumSort = e.target.value;
  loadAlbums();
});

// ── Actions ───────────────────────────────────

function startJob(jobId, fetchFn) {
  currentJobId = jobId;
  fetchFn().then(() => {
    showJobBanner("Running…");
    pollJob(jobId);
  }).catch(err => alert(`Error: ${err.message}`));
}

el("btn-sync").addEventListener("click", e => {
  e.preventDefault();
  startJob("sync", () => apiFetch("/api/sync", { method: "POST" }));
});

el("btn-enrich").addEventListener("click", e => {
  e.preventDefault();
  startJob("enrich", () => apiFetch("/api/enrich", { method: "POST" }));
});

el("btn-lyrics-new").addEventListener("click", e => {
  e.preventDefault();
  startJob("fetch_lyrics", () => apiFetch("/api/fetch-lyrics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ batch: 50 }),
  }));
});

el("btn-lyrics-failed").addEventListener("click", e => {
  e.preventDefault();
  startJob("fetch_lyrics", () => apiFetch("/api/fetch-lyrics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ batch: 50, retry_failed: true }),
  }));
});

el("btn-lyrics-all").addEventListener("click", e => {
  e.preventDefault();
  startJob("fetch_lyrics", () => apiFetch("/api/fetch-lyrics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ batch: 50, retry_all: true }),
  }));
});

el("btn-summarise-ollama").addEventListener("click", e => {
  e.preventDefault();
  startJob("summarise", () => apiFetch("/api/summarise", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_type: "ollama", batch: 20 }),
  }));
});

el("btn-backfill-casual").addEventListener("click", e => {
  e.preventDefault();
  startJob("summarise_backfill_casual", () => apiFetch("/api/summarise", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_type: "ollama", batch: 20, mode: "backfill_casual" }),
  }));
});


// ── Job polling ───────────────────────────────

function showJobBanner(msg, state = "running") {
  const banner = el("job-banner");
  banner.classList.remove("hidden", "job-done", "job-error", "job-stopped");
  if (state === "done")    banner.classList.add("job-done");
  if (state === "error")   banner.classList.add("job-error");
  if (state === "stopped") banner.classList.add("job-stopped");
  el("job-message").textContent = msg;

  const running = state === "running";
  el("job-stop").classList.toggle("hidden", !running);
  el("job-dismiss").classList.toggle("hidden", running);
}

function hideJobBanner() {
  el("job-banner").classList.add("hidden");
  el("job-banner").classList.remove("job-done", "job-error", "job-stopped");
  clearInterval(jobPollTimer);
}

function updateJobOutput(text) {
  const out = el("job-output");
  out.textContent = text || "";
  out.scrollTop = out.scrollHeight;

  // Extract last meaningful line as the current step indicator
  const lines = (text || "").split("\n").map(l => l.trim()).filter(Boolean);
  el("job-current-step").textContent = lines.length ? lines[lines.length - 1] : "";
}

function pollJob(jobId) {
  clearInterval(jobPollTimer);
  jobPollTimer = setInterval(async () => {
    try {
      const job = await apiFetch(`/api/job/${jobId}`);
      if (job.status === "running") {
        showJobBanner("Running…");
        updateJobOutput(job.output);
        loadStats();
      } else if (job.status === "done") {
        clearInterval(jobPollTimer);
        showJobBanner("Complete ✓", "done");
        updateJobOutput(job.output);
        loadStats();
        loadTracks();
        loadTags();
        loadAlbums();
      } else if (job.status === "stopped") {
        clearInterval(jobPollTimer);
        showJobBanner("Stopped", "stopped");
        updateJobOutput(job.output || "(stopped)");
      } else if (job.status === "error") {
        clearInterval(jobPollTimer);
        showJobBanner("Job failed", "error");
        updateJobOutput(job.output || "(no output)");
      }
    } catch (_) {}
  }, 2000);
}

el("job-stop").addEventListener("click", () => {
  if (currentJobId) {
    apiFetch(`/api/stop/${currentJobId}`, { method: "POST" }).catch(() => {});
  }
});

el("job-dismiss").addEventListener("click", () => hideJobBanner());

// ── Debug view ────────────────────────────────

let debugVisible = false;

function showDebugView() {
  debugVisible = true;
  el("debug-view").classList.remove("hidden");
  document.querySelector(".layout").classList.add("hidden");
  el("btn-debug").classList.add("active");
  loadDebugHealth();
}

function hideDebugView() {
  debugVisible = false;
  el("debug-view").classList.add("hidden");
  document.querySelector(".layout").classList.remove("hidden");
  el("btn-debug").classList.remove("active");
}

el("btn-debug").addEventListener("click", () => {
  if (debugVisible) hideDebugView(); else showDebugView();
});

el("btn-debug-back").addEventListener("click", hideDebugView);

async function loadDebugHealth() {
  const grid = el("debug-health-grid");
  try {
    const h = await apiFetch("/api/debug/health");
    const cards = [
      { label: "Total tracks",   value: h.total_tracks,    cls: "stat-info" },
      { label: "Total albums",   value: h.total_albums,    cls: "stat-info" },
      { label: "With lyrics",    value: h.with_lyrics,     cls: h.with_lyrics  === h.total_tracks ? "stat-ok" : "stat-warn" },
      { label: "No lyrics",      value: h.no_lyrics,       cls: h.no_lyrics    === 0 ? "stat-ok" : "stat-warn" },
      { label: "Pending lyrics", value: h.pending_lyrics,  cls: h.pending_lyrics === 0 ? "stat-ok" : "stat-warn" },
      { label: "With summary",   value: h.with_summary,    cls: h.with_summary === h.with_lyrics ? "stat-ok" : "stat-warn" },
      { label: "With tags",      value: h.with_tags,       cls: h.with_tags    === h.with_summary ? "stat-ok" : "stat-warn" },
      { label: "Pending summary",value: h.pending_summary, cls: h.pending_summary === 0 ? "stat-ok" : "stat-warn" },
      { label: "Stuck (no data)",value: h.stuck_summary,   cls: h.stuck_summary  === 0 ? "stat-ok" : "stat-bad" },
    ];
    grid.innerHTML = cards.map(({ label, value, cls }) => `
      <div class="debug-stat-card ${cls}">
        <div class="stat-value">${value}</div>
        <div class="stat-label">${label}</div>
      </div>
    `).join("");
  } catch (err) {
    grid.innerHTML = `<p class="muted">Failed to load health data: ${escHtml(err.message)}</p>`;
  }
}

el("btn-reset-stuck").addEventListener("click", async () => {
  const result = el("reset-stuck-result");
  result.textContent = "Resetting…";
  try {
    const r = await apiFetch("/api/debug/reset-stuck", { method: "POST" });
    result.textContent = `Reset ${r.reset} track(s). Re-run Summarise to process them.`;
    loadDebugHealth();
  } catch (err) {
    result.textContent = `Error: ${err.message}`;
  }
});

el("dbg-btn-summarise-ollama").addEventListener("click", () => {
  startJob("summarise", () => apiFetch("/api/summarise", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_type: "ollama", batch: 20 }),
  }));
});

el("dbg-btn-backfill-casual").addEventListener("click", () => {
  startJob("summarise_backfill_casual", () => apiFetch("/api/summarise", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_type: "ollama", batch: 20, mode: "backfill_casual" }),
  }));
});


el("dbg-btn-lyrics-new").addEventListener("click", () => {
  startJob("fetch_lyrics", () => apiFetch("/api/fetch-lyrics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ batch: 50 }),
  }));
});

el("dbg-btn-lyrics-failed").addEventListener("click", () => {
  startJob("fetch_lyrics", () => apiFetch("/api/fetch-lyrics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ batch: 50, retry_failed: true }),
  }));
});

el("dbg-btn-lyrics-all").addEventListener("click", () => {
  startJob("fetch_lyrics", () => apiFetch("/api/fetch-lyrics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ batch: 50, retry_all: true }),
  }));
});

el("dbg-btn-sync").addEventListener("click", () => {
  startJob("sync", () => apiFetch("/api/sync", { method: "POST" }));
});

el("dbg-btn-enrich").addEventListener("click", () => {
  startJob("enrich", () => apiFetch("/api/enrich", { method: "POST" }));
});

// ── Helpers ───────────────────────────────────

function escHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Sidebar toggle (mobile) ───────────────────

const sidebarToggle = el("sidebar-toggle");
if (sidebarToggle) {
  const doToggle = () => {
    const aside = document.querySelector("aside");
    const open = aside.classList.toggle("sidebar-open");
    aside.style.display = open ? "block" : "";
    sidebarToggle.setAttribute("aria-expanded", String(open));
  };
  sidebarToggle.addEventListener("click", doToggle);
}

// ── Tag merge ─────────────────────────────────

el("btn-suggest-merges").addEventListener("click", async () => {
  const btn = el("btn-suggest-merges");
  const resultsEl = el("tag-merge-results");
  const model = el("tag-merge-model").value;

  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  resultsEl.innerHTML = "<p class='muted'>Asking AI to analyse tags…</p>";

  try {
    const data = await apiFetch("/api/tags/suggest-merges", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_type: model }),
    });

    if (data.error) {
      resultsEl.innerHTML = `<p style="color:var(--pico-color-red-500,#ef4444)">${escHtml(data.error)}</p>`;
      return;
    }

    const suggestions = data.suggestions || [];
    if (!suggestions.length) {
      resultsEl.innerHTML = "<p class='muted'>No near-duplicate tags found.</p>";
      return;
    }

    resultsEl.innerHTML = "";
    const list = document.createElement("div");
    list.className = "tag-merge-list";

    suggestions.forEach(s => {
      const row = document.createElement("div");
      row.className = "tag-merge-row";
      row.innerHTML = `
        <div class="tag-merge-info">
          <span class="tag-pill">${escHtml(s.remove)}</span>
          <span class="tag-merge-arrow">→</span>
          <span class="tag-pill">${escHtml(s.keep)}</span>
          <span class="tag-merge-reason">${escHtml(s.reason)}</span>
        </div>
        <div class="tag-merge-actions">
          <button class="outline btn-do-merge" style="font-size:0.8rem;padding:0.2rem 0.6rem;margin:0">Merge</button>
          <button class="outline secondary btn-skip-merge" style="font-size:0.8rem;padding:0.2rem 0.6rem;margin:0">Skip</button>
        </div>
      `;

      row.querySelector(".btn-do-merge").addEventListener("click", async () => {
        const mergeBtn = row.querySelector(".btn-do-merge");
        mergeBtn.setAttribute("aria-busy", "true");
        mergeBtn.disabled = true;
        try {
          const result = await apiFetch("/api/tags/merge", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ keep: s.keep, remove: s.remove }),
          });
          row.innerHTML = `<span class="muted">✓ Merged "${escHtml(s.remove)}" → "${escHtml(s.keep)}" (${result.merged_count} track(s) updated)</span>`;
          loadTags();
        } catch (err) {
          mergeBtn.removeAttribute("aria-busy");
          mergeBtn.disabled = false;
          row.querySelector(".tag-merge-reason").textContent = `Error: ${err.message}`;
        }
      });

      row.querySelector(".btn-skip-merge").addEventListener("click", () => {
        row.remove();
        if (!list.querySelector(".tag-merge-row")) {
          resultsEl.innerHTML = "<p class='muted'>All suggestions reviewed.</p>";
        }
      });

      list.appendChild(row);
    });

    resultsEl.appendChild(list);
  } catch (err) {
    resultsEl.innerHTML = `<p style="color:var(--pico-color-red-500,#ef4444)">Error: ${escHtml(err.message)}</p>`;
  } finally {
    btn.removeAttribute("aria-busy");
    btn.disabled = false;
  }
});

// ── Group tags ────────────────────────────────

el("btn-group-tags").addEventListener("click", async () => {
  const btn = el("btn-group-tags");
  const resultsEl = el("tag-group-results");
  const model = el("tag-group-model").value;

  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  resultsEl.innerHTML = "<p class='muted'>Asking AI to group tags…</p>";

  try {
    const data = await apiFetch("/api/group-tags", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_type: model }),
    });

    if (data.error) {
      resultsEl.innerHTML = `<p style="color:var(--pico-color-red-500,#ef4444)">${escHtml(data.error)}</p>`;
      return;
    }

    const jobId = data.job_id;
    resultsEl.innerHTML = "<p class='muted'>Job started — polling for output…</p>";

    const poll = setInterval(async () => {
      try {
        const job = await apiFetch(`/api/job/${jobId}`);
        const lines = escHtml(job.output || "").replace(/\n/g, "<br>");
        resultsEl.innerHTML = `<pre style="font-size:0.8rem;white-space:pre-wrap">${lines}</pre>`;
        if (job.status === "done" || job.status === "error" || job.status === "stopped") {
          clearInterval(poll);
          btn.removeAttribute("aria-busy");
          btn.disabled = false;
          // Reload themes dropdown
          const themeSel = el("theme-filter");
          themeSel.innerHTML = '<option value="">All themes</option>';
          el("theme-filter-row").classList.add("hidden");
          loadThemes();
        }
      } catch (_) {}
    }, 2000);
  } catch (err) {
    resultsEl.innerHTML = `<p style="color:var(--pico-color-red-500,#ef4444)">Error: ${escHtml(err.message)}</p>`;
    btn.removeAttribute("aria-busy");
    btn.disabled = false;
  }
});

// ── Init ──────────────────────────────────────

(async () => {
  await Promise.all([loadStats(), loadAlbums(), loadTags()]);
  loadThemes();
  await loadTracks();
})();
