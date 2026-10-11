/* Set -> Playlist front end. Vanilla JS, talks to the Flask JSON API. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const CONCURRENCY = 3;

  const state = {
    status: null,
    video: null,
    source: null,
    setArtist: null,
    rows: [], // { track, match, selected, busy }
    matching: false,
  };

  // ---------------------------------------------------------------- API
  async function api(path, payload) {
    const opts = payload === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
    const res = await fetch(path, opts);
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON error page */ }
    if (!res.ok) {
      const err = new Error(data.error || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ---------------------------------------------------------------- alerts
  function alertBox(html, kind = "warn", { id, dismissAfter } = {}) {
    const box = document.createElement("div");
    box.className = `alert ${kind}`;
    box.innerHTML = html;
    if (id) {
      const old = document.getElementById(id);
      if (old) old.remove();
      box.id = id;
    }
    $("#alerts").appendChild(box);
    if (dismissAfter) setTimeout(() => box.remove(), dismissAfter);
    return box;
  }
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function showError(err) {
    if (err.status === 401) refreshStatus();
    alertBox(esc(err.message || err), "error", { id: "last-error", dismissAfter: 12000 });
  }

  function setBusy(btn, busy, label) {
    if (busy) {
      btn.dataset.label = btn.textContent;
      btn.innerHTML = `<span class="spinner"></span>${esc(label || btn.textContent)}`;
      btn.disabled = true;
    } else {
      btn.textContent = btn.dataset.label || btn.textContent;
      btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------- status / account
  async function refreshStatus() {
    try {
      state.status = await api("/api/status");
    } catch (err) {
      $("#account").innerHTML = `<span class="muted">Server unreachable</span>`;
      return;
    }
    const s = state.status;
    const acct = $("#account");
    if (!s.spotify.configured) {
      acct.innerHTML = `<span class="muted">Spotify not configured</span>`;
      alertBox(
        `Spotify credentials are missing: <code>${s.missing.map(esc).join("</code>, <code>")}</code>. ` +
        `Copy <code>.env.example</code> to <code>.env</code>, fill it in and restart the server.`,
        "error", { id: "cfg-alert" });
    } else if (s.spotify.connected) {
      const u = s.spotify.user;
      acct.innerHTML =
        (u.image ? `<img class="avatar" src="${esc(u.image)}" alt="">` : "") +
        `<span>${esc(u.name)}</span><a class="btn small ghost" href="/logout">Log out</a>`;
    } else {
      acct.innerHTML = `<a class="btn small primary" href="/login">Connect Spotify</a>`;
    }

    if (!s.youtube.api_key) {
      alertBox(`No <code>YOUTUBE_API_KEY</code> set, so videos are read with yt-dlp. That works, but it's slower.`,
        "warn", { id: "yt-alert", dismissAfter: 10000 });
    }
    if (location.origin !== s.expected_origin && s.spotify.configured) {
      alertBox(`Open this app at <a href="${esc(s.expected_origin)}">${esc(s.expected_origin)}</a> so the Spotify login redirect (<code>${esc(s.redirect_uri)}</code>) works.`,
        "warn", { id: "origin-alert" });
    }
    updateControls();
  }

  // ---------------------------------------------------------------- step 1: load
  $("#load-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = $("#url").value.trim();
    const tracklist = $("#manual").value.trim();
    if (!url && !tracklist) { $("#url").focus(); return; }
    const btn = $("#load-btn");
    setBusy(btn, true, "Loading…");
    try {
      const data = await api("/api/tracklist", { url, tracklist });
      state.video = data.video;
      state.source = data.source;
      state.setArtist = data.set_artist || null;
      state.rows = data.tracks.map((t) => ({ track: t, match: null, selected: false, busy: false }));
      renderVideo();
      renderRows();
      $("#pl-name").value = data.video.title || "DJ Set";
      $("#pl-desc").value = data.default_description || "";
      $("#result").innerHTML = "";
      $("#step-match").classList.remove("hidden");
      $("#step-create").classList.remove("hidden");
      if (!data.tracks.length) {
        alertBox("No tracklist found in the description or comments. Paste it into the box under step 1 and try again.", "warn",
          { id: "empty-alert" });
        $(".manual").open = true;
      } else if (state.status?.spotify.connected) {
        matchAll(); // go straight on to matching
      }
      $("#step-match").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      showError(err);
    } finally {
      setBusy(btn, false);
    }
  });

  function renderVideo() {
    const v = state.video;
    const sourceLabel = { description: "description", comments: "comments", chapters: "chapters", manual: "pasted text" }[state.source] || state.source;
    $("#video").innerHTML = `
      ${v.thumbnail ? `<img src="${esc(v.thumbnail)}" alt="">` : ""}
      <div>
        <h3>${v.url ? `<a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.title)}</a>` : esc(v.title)}</h3>
        <div class="muted small">${esc(v.channel || "")}</div>
        <div style="margin-top:.4rem">
          <span class="chip">${state.rows.length} tracks</span>
          <span class="chip">from ${esc(sourceLabel)}</span>
          ${v.source && v.source !== "manual" ? `<span class="chip">via ${esc(v.source)}</span>` : ""}
        </div>
      </div>`;
  }

  // ---------------------------------------------------------------- step 2: table
  function renderRows() {
    const body = $("#track-rows");
    body.textContent = "";
    state.rows.forEach((row, i) => body.appendChild(buildRow(row, i)));
    renderStats();
  }

  function buildRow(row, i) {
    const tr = $("#row-tpl").content.firstElementChild.cloneNode(true);
    tr.dataset.i = i;
    paintRow(tr, row);

    const sel = $(".sel", tr);
    sel.addEventListener("change", () => { row.selected = sel.checked; renderStats(); });

    const form = $(".edit", tr);
    const input = $(".edit-input", tr);
    $(".edit-btn", tr).addEventListener("click", () => {
      form.classList.toggle("hidden");
      input.value = row.track.artist ? `${row.track.artist} - ${row.track.title}` : row.track.title;
      if (!form.classList.contains("hidden")) input.focus();
    });
    $(".cancel", tr).addEventListener("click", () => form.classList.add("hidden"));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const q = input.value.trim();
      if (!q) return;
      form.classList.add("hidden");
      await matchRow(i, { query: q, index: row.track.index, track: row.track });
    });
    return tr;
  }

  function paintRow(tr, row) {
    const t = row.track, m = row.match;
    $(".c-num", tr).textContent = t.index;
    $(".c-time", tr).textContent = t.timestamp || "";
    $(".src-main", tr).innerHTML = t.artist
      ? `<span class="artist">${esc(t.artist)}</span> - ${esc(t.title)}`
      : esc(t.title);
    const raw = $(".src-raw", tr);
    raw.textContent = t.raw && t.raw !== t.display ? t.raw : "";

    const sel = $(".sel", tr);
    sel.disabled = !(m && m.uri);
    sel.checked = !!row.selected;

    const cell = $(".c-match", tr);
    tr.className = m ? m.status : "";
    if (row.busy) {
      cell.innerHTML = `<span class="muted small"><span class="spinner" style="display:inline-block;vertical-align:-2px"></span> Searching…</span>`;
    } else if (!m) {
      cell.innerHTML = `<span class="muted small">Not searched yet</span>`;
    } else if (!m.uri) {
      cell.innerHTML = `<span class="small">No match</span><span class="badge unmatched">missing</span>`;
    } else {
      const pct = Math.round(m.score * 100);
      cell.innerHTML = `
        <div class="match">
          ${m.image ? `<img src="${esc(m.image)}" alt="" loading="lazy">` : ""}
          <div>
            <a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.name)}</a>
            <span class="badge ${esc(m.status)}" title="Match confidence">${m.status === "matched" ? pct + "%" : "check · " + pct + "%"}</span>
            <div class="small match-artists">${esc(m.artists.join(", "))}${m.album ? " · " + esc(m.album) : ""}</div>
          </div>
        </div>`;
    }
  }

  function repaint(i) {
    const tr = $(`#track-rows tr[data-i="${i}"]`);
    if (tr) paintRow(tr, state.rows[i]);
    renderStats();
  }

  async function matchRow(i, payload) {
    const row = state.rows[i];
    row.busy = true;
    repaint(i);
    try {
      const m = await api("/api/search", { ...(payload || { track: row.track }), set_artist: state.setArtist });
      row.match = m;
      if (payload?.query) row.track = { ...m.track, raw: row.track.raw }; // keep the user's edit
      row.selected = m.status === "matched" || (payload?.query && !!m.uri);
    } catch (err) {
      row.match = row.match || null;
      if (err.status === 401 || err.status === 403) throw err;
      showError(err);
    } finally {
      row.busy = false;
      repaint(i);
    }
  }

  async function matchAll() {
    if (state.matching) return;
    if (!state.status?.spotify.connected) {
      alertBox(`Connect Spotify first. <a href="/login">Connect now</a>`, "warn", { id: "login-alert" });
      return;
    }
    state.matching = true;
    const btn = $("#match-btn");
    setBusy(btn, true, "Matching…");
    const bar = $("#progress-bar");
    $("#progress").classList.remove("hidden");
    bar.style.width = "0%";

    const queue = state.rows.map((_, i) => i);
    let done = 0;
    let fatal = null;
    const worker = async () => {
      while (queue.length && !fatal) {
        const i = queue.shift();
        try { await matchRow(i); } catch (err) { fatal = err; }
        done++;
        bar.style.width = `${(done / state.rows.length) * 100}%`;
      }
    };
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
    if (fatal) showError(fatal);

    state.matching = false;
    setBusy(btn, false);
    btn.textContent = "Re-match all";
    setTimeout(() => $("#progress").classList.add("hidden"), 600);
  }
  $("#match-btn").addEventListener("click", matchAll);

  $("#check-all").addEventListener("change", (e) => {
    state.rows.forEach((r) => { if (r.match?.uri) r.selected = e.target.checked; });
    renderRows();
  });

  function renderStats() {
    const rows = state.rows;
    const count = (s) => rows.filter((r) => r.match?.status === s).length;
    const selected = rows.filter((r) => r.selected && r.match?.uri).length;
    $("#stats").innerHTML = rows.length ? `
      <span><b>${count("matched")}</b> matched</span>
      <span><b>${count("uncertain")}</b> need a check</span>
      <span><b>${count("unmatched")}</b> not found</span>
      <span><b>${selected}</b> selected for the playlist</span>` : "";
    const create = $("#create-btn");
    if (!create.querySelector(".spinner")) create.textContent = selected ? `Create playlist (${selected})` : "Create playlist";
    updateControls();
  }

  function updateControls() {
    const connected = !!state.status?.spotify.connected;
    $("#match-btn").disabled = state.matching || !connected || !state.rows.length;
    const selected = state.rows.some((r) => r.selected && r.match?.uri);
    if (!$("#create-btn").querySelector(".spinner")) $("#create-btn").disabled = !connected || !selected;
  }

  // ---------------------------------------------------------------- step 3: create
  function unmatchedRows() {
    return state.rows.filter((r) => !(r.selected && r.match?.uri));
  }

  $("#create-btn").addEventListener("click", async () => {
    const chosen = state.rows.filter((r) => r.selected && r.match?.uri);
    if (!chosen.length) return;
    const btn = $("#create-btn");
    setBusy(btn, true, "Creating…");
    try {
      const pl = await api("/api/playlist", {
        name: $("#pl-name").value.trim() || state.video?.title || "DJ Set",
        description: $("#pl-desc").value.trim(),
        public: $("#pl-public").checked,
        uris: chosen.map((r) => r.match.uri),
        unmatched: unmatchedRows().map((r) => r.track),
        video_id: state.video?.video_id || "",
      });
      $("#result").innerHTML = `
        <div class="alert ok">
          Created <b>${esc(pl.name)}</b> with ${pl.added} tracks.
          ${pl.url ? `<a class="btn small primary" style="margin-left:.5rem" href="${esc(pl.url)}" target="_blank" rel="noopener">Open in Spotify</a>` : ""}
          ${pl.unmatched_file ? `<div class="small muted" style="margin-top:.35rem">Unmatched tracks saved to <code>${esc(pl.unmatched_file)}</code></div>` : ""}
        </div>`;
    } catch (err) {
      showError(err);
    } finally {
      setBusy(btn, false);
      renderStats();
    }
  });

  $("#unmatched-btn").addEventListener("click", () => {
    const rows = unmatchedRows();
    const lines = [
      `Unmatched tracks for: ${state.video?.title || "DJ Set"}`,
      state.video?.url ? `Video: ${state.video.url}` : "",
      "",
      ...rows.map((r) => `${r.track.timestamp ? `[${r.track.timestamp}] ` : ""}${r.track.display}` +
        (r.match?.status === "uncertain" ? `  (closest: ${r.match.artists.join(", ")} - ${r.match.name})` : "")),
    ];
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `unmatched-${(state.video?.title || "set").replace(/[^\w]+/g, "-").slice(0, 50)}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  });

  // ---------------------------------------------------------------- step 4: transition checker
  // Camelot wheel: compatible = same key, same number (A<->B), or +/-1 with the same letter.
  function parseKey(raw) {
    const m = String(raw).trim().toUpperCase().match(/^(1[0-2]|[1-9])\s*([AB])$/);
    return m ? { n: Number(m[1]), l: m[2] } : null;
  }
  function keyRelation(a, b) {
    if (a.n === b.n && a.l === b.l) return "same";
    if (a.n === b.n) return "relative";
    const step = (b.n - a.n + 12) % 12;
    if (a.l === b.l && (step === 1 || step === 11)) return "neighbour";
    if (a.l === b.l && step === 2) return "boost"; // +2: energy boost, usable but punchy
    return "clash";
  }

  function recommend(bpmA, keyA, bpmB, keyB) {
    const diff = bpmB - bpmA;
    const pct = (Math.abs(diff) / Math.max(bpmA, bpmB)) * 100;
    const rel = keyRelation(keyA, keyB);
    const keysOk = rel !== "clash";
    const notes = [];

    notes.push(`Tempo ${diff === 0 ? "is identical" : `${diff > 0 ? "goes up" : "drops"} ${Math.abs(diff).toFixed(1).replace(/\.0$/, "")} BPM (${pct.toFixed(1)}%)`}.`);
    notes.push({
      same: "Same key: the songs will sit perfectly on top of each other.",
      relative: "Relative keys (same number): they blend smoothly.",
      neighbour: "Neighbouring keys on the wheel: they blend well.",
      boost: "Two steps up the wheel: works, and adds an energy lift.",
      clash: "Keys clash: don't let both songs play together for long.",
    }[rel]);

    if (pct > 5 || !keysOk) {
      notes.push("Keep the overlap short so the tempo or key difference isn't heard.");
      return { preset: "Fade", bars: "4 bars", tone: "warn", notes };
    }
    if (pct <= 3 && diff > 1) {
      notes.push("The next song is a bit faster, so a Rise builds energy into it.");
      return { preset: "Rise", bars: "8 bars", tone: "ok", notes };
    }
    if (pct <= 3) {
      notes.push("Long blend with a bass swap, the classic DJ transition.");
      return { preset: "Blend", bars: "16 bars", tone: "ok", notes };
    }
    notes.push("Medium gap. Spotify's Auto setting usually handles this well; preview it first.");
    return { preset: "Auto", bars: "8 bars", tone: "ok", notes };
  }

  $("#checker-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const out = $("#checker-result");
    const bpmA = parseFloat($("#ck-bpm-a").value), bpmB = parseFloat($("#ck-bpm-b").value);
    const keyA = parseKey($("#ck-key-a").value), keyB = parseKey($("#ck-key-b").value);
    if (!(bpmA > 0 && bpmB > 0)) { out.innerHTML = `<div class="alert error">Enter both BPMs.</div>`; return; }
    if (!keyA || !keyB) { out.innerHTML = `<div class="alert error">Keys look like <code>8A</code> or <code>11B</code> (1–12 plus A or B), as shown in Spotify.</div>`; return; }
    const r = recommend(bpmA, keyA, bpmB, keyB);
    out.innerHTML = `
      <div class="verdict">
        <div class="verdict-preset" style="color:var(${r.tone === "warn" ? "--warn" : "--accent"})">${esc(r.preset)}<small>${esc(r.bars)}</small></div>
        <ul>${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
      </div>`;
  });

  // ---------------------------------------------------------------- boot
  const authError = new URLSearchParams(location.search).get("auth_error");
  if (authError) {
    alertBox(`Spotify login failed: <code>${esc(authError)}</code>. Check the redirect URI in your Spotify app settings.`, "error");
    history.replaceState(null, "", "/");
  }
  refreshStatus();
})();
