const state = {
  today: null,
  minDate: null,
  currentDate: null,
  oldestLoadedDate: null,
  loadedEditions: [],
  activeTopic: "all",
  activeSourceGroup: "all",
  searchQuery: "",
  hasGeminiKey: false,
  maskedKey: "",
};

function addDays(dateStr, deltaDays) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + deltaDays);
  return dt.toISOString().slice(0, 10);
}

function clampDate(dateStr) {
  if (state.today && dateStr > state.today) return state.today;
  if (state.minDate && dateStr < state.minDate) return state.minDate;
  return dateStr;
}

async function fetchConfig() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  state.today = cfg.today;
  state.minDate = cfg.min_date;
  state.hasGeminiKey = cfg.has_gemini_key;
  state.maskedKey = cfg.masked_gemini_key;

  const picker = document.getElementById("datePickerInput");
  picker.max = state.today;
  picker.min = state.minDate;

  document.getElementById("minDateBoundLabel").textContent = state.minDate;
  updateKeyStatusUI();
  renderCachedDates(cfg.cached_dates || []);
}

function updateKeyStatusUI() {
  const badge = document.getElementById("geminiStatusBadge");
  const sidebarStatus = document.getElementById("sidebarKeyStatus");
  if (state.hasGeminiKey) {
    badge.textContent = `Summarizer: Gemini 3.8 Flash (${state.maskedKey})`;
    badge.style.color = "#88ffaa";
    sidebarStatus.textContent = `Active (${state.maskedKey})`;
    sidebarStatus.style.color = "#006600";
  } else {
    badge.textContent = "Summarizer: Deterministic Rule Engine (No Key)";
    badge.style.color = "#ffdd88";
    sidebarStatus.textContent = "Rule Engine (Add Key for LLM)";
    sidebarStatus.style.color = "#664400";
  }
}

function renderCachedDates(dates) {
  const container = document.getElementById("cachedDatesList");
  if (!dates.length) {
    container.innerHTML = `<span class="muted">No cached dates yet.</span>`;
    return;
  }
  container.innerHTML = dates
    .map(
      (d) =>
        `<button class="cached-date-chip ${d === state.currentDate ? "active" : ""}" data-date="${d}">${d}</button>`
    )
    .join("");

  container.querySelectorAll(".cached-date-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      loadSingleDay(btn.dataset.date, false);
    });
  });
}

function setLoading(isLoading, customMessage = null) {
  const box = document.getElementById("loadingIndicator");
  const txt = document.getElementById("loadingText");
  if (customMessage) txt.textContent = customMessage;
  if (isLoading) {
    box.classList.remove("hidden");
  } else {
    box.classList.add("hidden");
  }
}

async function loadSingleDay(dateStr, forceRefresh = false) {
  const target = clampDate(dateStr);
  state.currentDate = target;
  state.oldestLoadedDate = target;

  document.getElementById("datePickerInput").value = target;
  updateNavButtons();

  setLoading(
    true,
    forceRefresh
      ? `Re-curating ${target} across all 7 sources (CNX Software, Hackaday, Substack/Memfault Newsletters, IEEE/ACM OpenAlex, HF Daily, arXiv & HN)...`
      : `Loading Embedded AI edition for ${target}...`
  );

  try {
    const res = await fetch(
      `/api/day/${target}?force_refresh=${forceRefresh ? "true" : "false"}`
    );
    const edition = await res.json();
    state.loadedEditions = [edition];
    renderFeed();
    updateTelemetry(edition);
    await refreshCachedList();
  } catch (err) {
    console.error("Failed loading edition:", err);
  } finally {
    setLoading(false);
  }
}

async function appendOlderDay() {
  const baseDate = state.oldestLoadedDate || state.currentDate || state.today;
  const prevDate = clampDate(addDays(baseDate, -1));
  if (prevDate === baseDate) return;

  setLoading(true, `Time-Machine: Curating older edition for ${prevDate} across 7 sources...`);
  try {
    const res = await fetch(`/api/day/${prevDate}`);
    const edition = await res.json();
    state.oldestLoadedDate = edition.date;
    state.loadedEditions.push(edition);
    renderFeed();
    updateTelemetry(edition);
    await refreshCachedList();
  } catch (err) {
    console.error("Failed appending older day:", err);
  } finally {
    setLoading(false);
  }
}

async function refreshCachedList() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    renderCachedDates(cfg.cached_dates || []);
  } catch (_) {}
}

function updateNavButtons() {
  document.getElementById("nextDayBtn").disabled = state.currentDate >= state.today;
  document.getElementById("todayBtn").disabled = state.currentDate === state.today;
  document.getElementById("prevDayBtn").disabled = state.currentDate <= state.minDate;

  const oldest = state.oldestLoadedDate || state.currentDate || state.today;
  const nextOlder = clampDate(addDays(oldest, -1));
  document.getElementById("oldestLoadedDateLabel").textContent = oldest;
  document.getElementById("nextOlderDatePreview").textContent = nextOlder;
  document.getElementById("appendPreviousDayBtn").disabled = oldest <= state.minDate;
  document.getElementById("switchPreviousDayBtn").disabled = state.currentDate <= state.minDate;
}

function updateTelemetry(edition) {
  if (!edition) return;
  document.getElementById("statDate").textContent = edition.date;
  document.getElementById("statNewsletters").textContent =
    edition.stats?.newsletters_blogs_scanned ?? "-";
  document.getElementById("statArxiv").textContent = edition.stats?.arxiv_scanned ?? "-";
  document.getElementById("statIeeeHf").textContent =
    edition.stats?.ieee_hf_scanned ?? "-";
  document.getElementById("statHn").textContent = edition.stats?.hn_scanned ?? "-";
  document.getElementById("statSelected").textContent = edition.stats?.selected_count ?? "-";
  document.getElementById("statScore").textContent = edition.stats?.avg_relevance_score ?? "-";
  document.getElementById("statEngine").textContent =
    edition.summarizer_mode === "gemini-3.8-flash"
      ? "Gemini 3.8 Flash"
      : "Deterministic Rule Engine";

  const banner = document.getElementById("editionBanner");
  banner.innerHTML = `Edition: <strong>${edition.display_date}</strong> &bull; ${edition.stories.length} Dispatches from 7 Sources &bull; Engine: <code>${edition.summarizer_mode}</code> ${edition.cached ? "(SQLite Cached)" : "(Freshly Curated)"}`;
}

function storyMatchesFilters(story) {
  if (state.activeTopic !== "all" && story.topic?.id !== state.activeTopic) {
    return false;
  }
  if (state.activeSourceGroup !== "all") {
    const st = story.source_type || "";
    if (
      state.activeSourceGroup === "newsletters" &&
      st !== "newsletter" &&
      st !== "newsletter_blog"
    ) {
      return false;
    }
    if (
      state.activeSourceGroup === "papers" &&
      st !== "arxiv" &&
      st !== "ieee_acm" &&
      st !== "hf_daily"
    ) {
      return false;
    }
    if (state.activeSourceGroup === "hn" && st !== "news") {
      return false;
    }
  }
  if (state.searchQuery) {
    const q = state.searchQuery.toLowerCase();
    const hay = `${story.title} ${story.department} ${story.summary_html} ${story.source_label}`.toLowerCase();
    return hay.includes(q);
  }
  return true;
}

function renderFeed() {
  updateNavButtons();
  const container = document.getElementById("daysFeedContainer");
  let html = "";

  for (const edition of state.loadedEditions) {
    const visibleStories = (edition.stories || []).filter(storyMatchesFilters);

    html += `
      <div class="day-section-divider">
        <span>/. Edition for ${edition.display_date} (${edition.date})</span>
        <span class="day-meta-pill">${visibleStories.length}/${edition.stories.length} stories shown | ${edition.summarizer_mode}</span>
      </div>
    `;

    if (visibleStories.length === 0) {
      html += `
        <div class="story-card">
          <div class="story-body">
            No stories on ${edition.date} match the active topic/source/search filter. Try resetting filters to "All Embedded AI" and "All 7 Sources".
          </div>
        </div>
      `;
      continue;
    }

    for (const s of visibleStories) {
      const comments = s.comments || [];
      const pdfLink = s.pdf_url
        ? `<a href="${s.pdf_url}" target="_blank" rel="noopener">[Read PDF]</a>`
        : "";
      const hnLink = s.hn_url
        ? `<a href="${s.hn_url}" target="_blank" rel="noopener">[HN Thread]</a>`
        : "";

      html += `
        <article class="story-card" data-story-id="${s.id}">
          <div class="story-header">
            <div class="story-title-wrap">
              <a href="${s.url}" target="_blank" rel="noopener" class="story-title-link">${s.title}</a>
            </div>
            <span class="story-topic-badge" data-topic-filter="${s.topic?.id || "all"}" title="Filter by ${s.topic?.name || "Topic"}">
              [${s.topic?.name || "Embedded AI"}]
            </span>
          </div>

          <div class="story-byline">
            <div>
              <strong>Posted by ${s.editor}</strong> on ${s.time_str}
              <span class="story-dept">${s.department}</span>
            </div>
            <span class="story-signal-tag" title="Source &amp; Hardware Relevance Score">
              ${s.source_label} &bull; Signal: ${s.relevance_score}
            </span>
          </div>

          <div class="story-body">
            ${s.summary_html}
          </div>

          <div class="story-footer">
            <div class="story-footer-links">
              <a href="${s.url}" target="_blank" rel="noopener">🔗 ${s.source_label}</a>
              ${pdfLink}
              ${hnLink}
              <span>|</span>
              <button class="link-btn toggle-comments-btn" data-target="comments-${edition.date}-${s.id}">
                💬 ${comments.length} Comment${comments.length === 1 ? "" : "s"} (Threshold: +4)
              </button>
            </div>
            <div>
              <span style="color:#555555;">Curated via ${s.summarized_by}</span>
            </div>
          </div>

          <div id="comments-${edition.date}-${s.id}" class="comments-drawer">
            <div class="comments-list">
              ${comments
                .map(
                  (c) => `
                <div class="comment-item">
                  <div class="comment-head">
                    <span><strong>${c.title}</strong> (Score: <span class="comment-score">${c.score}</span>)</span>
                    <span>by <strong>${c.author}</strong> ${c.timestamp || ""}</span>
                  </div>
                  <div class="comment-body">${c.body}</div>
                </div>
              `
                )
                .join("")}
            </div>

            <form class="add-comment-form" data-date="${edition.date}" data-story="${s.id}">
              <div class="add-comment-row">
                <input type="text" name="title" placeholder="Reply subject (e.g. Re: DMA overhead on Cortex-M)..." required style="flex:2;" />
                <input type="text" name="author" placeholder="Handle (default: Anonymous Coward)" style="flex:1;" />
                <select name="score">
                  <option value="+5, Insightful">+5, Insightful</option>
                  <option value="+5, Informative">+5, Informative</option>
                  <option value="+4, Interesting">+4, Interesting</option>
                  <option value="+5, Funny">+5, Funny</option>
                </select>
              </div>
              <div class="add-comment-row">
                <textarea name="body" placeholder="Post your technical take..." required></textarea>
              </div>
              <div>
                <button type="submit" class="slash-small-btn primary">Post Comment</button>
              </div>
            </form>
          </div>
        </article>
      `;
    }
  }

  container.innerHTML = html;
  attachStoryEvents();
}

function attachStoryEvents() {
  document.querySelectorAll(".toggle-comments-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = document.getElementById(btn.dataset.target);
      if (target) target.classList.toggle("hidden");
    });
  });

  document.querySelectorAll(".story-topic-badge").forEach((badge) => {
    badge.addEventListener("click", () => {
      const topic = badge.dataset.topicFilter;
      setTopicFilter(topic);
    });
  });

  document.querySelectorAll(".add-comment-form").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const dateStr = form.dataset.date;
      const storyId = form.dataset.story;
      const fd = new FormData(form);
      const payload = {
        story_id: storyId,
        title: fd.get("title"),
        author: fd.get("author") || "Anonymous Coward",
        score: fd.get("score"),
        body: fd.get("body"),
      };

      const res = await fetch(`/api/day/${dateStr}/comment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        const ed = state.loadedEditions.find((x) => x.date === dateStr);
        if (ed) {
          const st = ed.stories.find((x) => x.id === storyId);
          if (st) {
            st.comments = st.comments || [];
            st.comments.push(data.comment);
            renderFeed();
          }
        }
      }
    });
  });
}

function setTopicFilter(topicId) {
  state.activeTopic = topicId;
  document.querySelectorAll(".utility-left a").forEach((a) => {
    const t = a.dataset.topic || "all";
    a.classList.toggle("active", t === topicId);
  });
  renderFeed();
}

function setupEventListeners() {
  document.getElementById("navAllTopics").addEventListener("click", (e) => {
    e.preventDefault();
    setTopicFilter("all");
  });
  document.querySelectorAll(".utility-left a[data-topic]").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      setTopicFilter(a.dataset.topic);
    });
  });

  document.getElementById("sourceFilterSelect").addEventListener("change", (e) => {
    state.activeSourceGroup = e.target.value;
    renderFeed();
  });

  document.getElementById("searchFilterInput").addEventListener("input", (e) => {
    state.searchQuery = e.target.value.trim();
    renderFeed();
  });

  document.getElementById("prevDayBtn").addEventListener("click", () => {
    loadSingleDay(addDays(state.currentDate, -1));
  });
  document.getElementById("nextDayBtn").addEventListener("click", () => {
    loadSingleDay(addDays(state.currentDate, 1));
  });
  document.getElementById("todayBtn").addEventListener("click", () => {
    loadSingleDay(state.today);
  });
  document.getElementById("datePickerInput").addEventListener("change", (e) => {
    if (e.target.value) loadSingleDay(e.target.value);
  });
  document.getElementById("refreshDayBtn").addEventListener("click", () => {
    loadSingleDay(state.currentDate, true);
  });

  document.querySelectorAll(".jump-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      const offset = parseInt(btn.dataset.offset, 10);
      const target = clampDate(addDays(state.today, -offset));
      loadSingleDay(target);
    });
  });

  document.getElementById("appendPreviousDayBtn").addEventListener("click", () => {
    appendOlderDay();
  });
  document.getElementById("switchPreviousDayBtn").addEventListener("click", () => {
    loadSingleDay(addDays(state.currentDate, -1));
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  document.getElementById("saveSidebarKeyBtn").addEventListener("click", async () => {
    const keyInput = document.getElementById("sidebarApiKeyInput");
    const msg = document.getElementById("keyFeedbackMsg");
    const val = keyInput.value.trim();
    if (!val) {
      msg.textContent = "Please paste a valid Gemini API key.";
      return;
    }
    const res = await fetch("/api/settings/gemini-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: val }),
    });
    const data = await res.json();
    state.hasGeminiKey = data.has_gemini_key;
    state.maskedKey = data.masked_gemini_key;
    keyInput.value = "";
    updateKeyStatusUI();
    msg.textContent = "Key saved! Re-summarizing current day with Gemini 3.8 Flash...";
    await loadSingleDay(state.currentDate, true);
    msg.textContent = "Edition summarized with Gemini 3.8 Flash!";
  });

  document.getElementById("clearSidebarKeyBtn").addEventListener("click", async () => {
    const res = await fetch("/api/settings/gemini-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: "" }),
    });
    const data = await res.json();
    state.hasGeminiKey = data.has_gemini_key;
    state.maskedKey = data.masked_gemini_key;
    updateKeyStatusUI();
    document.getElementById("keyFeedbackMsg").textContent =
      "Key cleared. Using deterministic rule engine.";
  });

  document.getElementById("openKeyModalBtn").addEventListener("click", () => {
    const input = document.getElementById("sidebarApiKeyInput");
    input.focus();
    input.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  document.getElementById("openPolicyModalBtn").addEventListener("click", () => {
    document.getElementById("policyModal").classList.remove("hidden");
  });

  document.querySelectorAll(".modal-close").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(btn.dataset.close).classList.add("hidden");
    });
  });

  document.getElementById("slashPollForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const resultsDiv = document.getElementById("pollResults");
    e.target.classList.add("hidden");
    resultsDiv.classList.remove("hidden");
    const tally = [
      { label: "On-chip SRAM (activations/KV)", pct: 38 },
      { label: "SPI Flash / LPDDR bandwidth", pct: 26 },
      { label: "Vendor NPU compilers & SDKs", pct: 21 },
      { label: "Accuracy below INT4", pct: 9 },
      { label: "Milliwatt thermal budget", pct: 6 },
    ];
    resultsDiv.innerHTML =
      tally
        .map(
          (t) => `
        <div class="poll-bar-row">
          <div><strong>${t.label}</strong> (${t.pct}%)</div>
          <div class="poll-bar-track"><div class="poll-bar-fill" style="width:${t.pct}%"></div></div>
        </div>
      `
        )
        .join("") + `<div class="small-note" style="margin-top:6px;">Total votes: 4,096 nerds</div>`;
  });
}

(async function init() {
  setupEventListeners();
  await fetchConfig();
  await loadSingleDay(state.today);
})();
