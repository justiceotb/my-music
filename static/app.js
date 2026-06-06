/* app.js - Music collection UI */

const state = {
  q: "",
  albumId: null,
  tag: null,
  filter: null,
  page: 1,
  trackSort: "artist",
  albumSort: "artist",
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
    loadTracks();
  });
}

// ── Tag cloud ─────────────────────────────────

async function loadTags() {
  try {
    const tags = await apiFetch("/api/tags");
    const container = el("tags");
    container.innerHTML = "";
    // Show top 40 tags
    tags.slice(0, 40).forEach(({ tag, count }) => {
      const span = document.createElement("span");
      span.className = "tag-pill";
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
        loadTracks();
      });
      container.appendChild(span);
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
  el("result-info").textContent = `${total} tracks found`;

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
        // Update tag cloud active state
        document.querySelectorAll("#tags .tag-pill").forEach(x => {
          x.classList.toggle("active", x.textContent.startsWith(state.tag));
        });
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

  pag.appendChild(mkBtn("‹ Prev", page - 1, page === 1));
  // Show a window of pages
  const start = Math.max(1, page - 2);
  const end   = Math.min(pages, page + 2);
  for (let p = start; p <= end; p++) {
    const btn = mkBtn(p, p, p === page);
    if (p === page) btn.classList.add("active");
    pag.appendChild(btn);
  }
  pag.appendChild(mkBtn("Next ›", page + 1, page === pages));
}

// ── Modal ─────────────────────────────────────

async function openModal(trackId) {
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
  el("modal-lyrics").textContent = t.lyrics || "(No lyrics yet)";
  el("track-modal").showModal();
}

el("modal-close").addEventListener("click", () => el("track-modal").close());
el("track-modal").addEventListener("click", e => {
  if (e.target === el("track-modal")) el("track-modal").close();
});

// ── Search ────────────────────────────────────

el("search").addEventListener("input", debounce(e => {
  state.q = e.target.value.trim();
  state.page = 1;
  loadTracks();
}, 300));

// ── Filter chips ─────────────────────────────

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
    loadTracks();
  });
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

el("btn-summarise-claude").addEventListener("click", e => {
  e.preventDefault();
  startJob("summarise", () => apiFetch("/api/summarise", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_type: "claude", batch: 20 }),
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

// ── Helpers ───────────────────────────────────

function escHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Init ──────────────────────────────────────

(async () => {
  await Promise.all([loadStats(), loadAlbums(), loadTags()]);
  await loadTracks();
})();
