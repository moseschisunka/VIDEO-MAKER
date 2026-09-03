import { el, fmtAgo, getJSON, subscribe, thumbURL } from "/ui/lib.js";

const grid = document.getElementById("grid");
const emptyState = document.getElementById("emptyState");
const projectSearch = document.getElementById("projectSearch");
const filterTabs = document.getElementById("filterTabs");

let allProjects = [];
let currentFilter = "all";
let searchQuery = "";
let availablePipelines = [];
let availablePlaybooks = [];
let availableVoices = [];
let releaseStatus = null;

function updateReleaseBanner() {
  const banner = document.getElementById("releaseBanner");
  if (!banner) return;
  const status = releaseStatus || {
    label: "Internal preview — production certification pending",
    production_gate: "PR-11G",
  };
  banner.innerHTML = "";
  banner.append(
    el("strong", {}, String(status.label || "Internal preview")),
    " · Production certification is locked until ",
    el("b", {}, String(status.production_gate || "PR-11G")),
    ". Rendered outputs remain uncertified while the readiness roadmap is in progress."
  );
}

// ---- Metrics Banner ------------------------------------------------
function updateMetrics(projects) {
  const total = projects.length;
  const live = projects.filter((p) => p.live || (p.active_stage && p.active_stage !== "publish")).length;
  const completed = projects.filter((p) => p.render_count > 0 || (p.completed_count >= 5)).length;
  const awaiting = projects.filter((p) => p.awaiting_human).length;

  document.getElementById("statTotalProjects").textContent = total;
  document.getElementById("statLivePipelines").textContent = live;
  document.getElementById("statCompletedVideos").textContent = completed;

  document.getElementById("badgeAll").textContent = total;
  document.getElementById("badgeLive").textContent = live;
  document.getElementById("badgeCompleted").textContent = completed;
  document.getElementById("badgeAwaiting").textContent = awaiting;

  const livePill = document.getElementById("livePill");
  if (live > 0) {
    livePill.classList.add("active");
    document.getElementById("liveStatusText").textContent = `${live} Active`;
  } else {
    livePill.classList.remove("active");
    document.getElementById("liveStatusText").textContent = "Live SSE";
  }
}

// ---- Project Card Component ---------------------------------------
function renderProjectCard(p) {
  const poster = el("div", { class: "lib-poster" });
  if (p.poster) {
    poster.append(el("img", { 
      src: thumbURL(p.project_id, p.poster, 640), 
      loading: "lazy", 
      alt: p.title || p.project_id 
    }));
  } else {
    poster.append(
      el("div", { class: "lp-placeholder" },
        el("div", { class: "placeholder-icon" }, "🎬"),
        el("span", { class: "lp-txt" }, "AWAITING RENDER")
      )
    );
  }

  // Live / Status Badges
  if (p.awaiting_human) {
    poster.append(el("span", { class: "lp-badge badge-amber" }, "◈ NEEDS APPROVAL"));
  } else if (p.live && p.active_stage) {
    poster.append(el("span", { class: "lp-badge badge-cyan" },
      el("span", { class: "pulse-dot" }),
      `STAGE: ${p.active_stage.toUpperCase()}`
    ));
  } else if (p.render_count > 0) {
    poster.append(el("span", { class: "lp-badge badge-emerald" }, "✓ RENDERED · NOT CERTIFIED"));
  }

  // Stage Progress Bar
  const totalStages = 6;
  const completedStages = p.completed_count || (p.render_count > 0 ? 6 : 1);
  const progressPercent = Math.min(100, Math.round((completedStages / totalStages) * 100));

  const progressRail = el("div", { class: "card-progress-rail" },
    el("div", { 
      class: "card-progress-fill", 
      style: `width: ${progressPercent}%` 
    })
  );

  const lane = p.release_lane || "experimental";
  const metaChips = el("div", { class: "card-meta-chips" },
    el("span", { class: "meta-chip chip-pipeline" }, p.pipeline_type || "animated-explainer"),
    el("span", { class: `meta-chip release-lane lane-${lane}` }, p.release_label || "Not production-certified"),
    p.scene_count ? el("span", { class: "meta-chip" }, `${p.scene_count} scenes`) : null,
    p.render_count ? el("span", { class: "meta-chip chip-renders" }, `${p.render_count} renders`) : null,
    el("span", { class: "meta-time" }, fmtAgo(p.last_activity))
  );

  const cardLink = el("a", { 
    class: `studio-card${p.live ? " card-live" : ""}`, 
    href: `/p/${p.project_id}`,
  },
    poster,
    progressRail,
    el("div", { class: "card-content" },
      el("div", { class: "card-header-row" },
        el("h3", { class: "card-title" }, p.title || p.project_id.replace(/-/g, " ")),
      ),
      metaChips,
      el("div", { class: "card-footer-row" },
        el("span", { class: "card-progress-text" }, `${progressPercent}% Completed`),
        el("span", { class: "btn-card-open" }, "Open Studio →")
      )
    )
  );

  return cardLink;
}

// ---- Filter & Search -----------------------------------------------
function filterProjects() {
  let filtered = allProjects.slice();

  if (currentFilter === "live") {
    filtered = filtered.filter((p) => p.live || (p.active_stage && p.active_stage !== "publish"));
  } else if (currentFilter === "completed") {
    filtered = filtered.filter((p) => p.render_count > 0 || p.completed_count >= 5);
  } else if (currentFilter === "awaiting") {
    filtered = filtered.filter((p) => p.awaiting_human);
  }

  if (searchQuery.trim()) {
    const q = searchQuery.toLowerCase();
    filtered = filtered.filter((p) => 
      (p.title && p.title.toLowerCase().includes(q)) ||
      (p.project_id && p.project_id.toLowerCase().includes(q)) ||
      (p.pipeline_type && p.pipeline_type.toLowerCase().includes(q))
    );
  }

  document.getElementById("resultsCount").textContent = `Showing ${filtered.length} of ${allProjects.length} productions`;
  grid.innerHTML = "";
  
  if (filtered.length === 0) {
    emptyState.style.display = "flex";
  } else {
    emptyState.style.display = "none";
    for (const p of filtered) {
      grid.append(renderProjectCard(p));
    }
  }
}

// ---- Render Main Library -------------------------------------------
async function refreshLibrary() {
  try {
    const requests = [getJSON("/api/projects")];
    if (!releaseStatus) requests.push(getJSON("/api/release-status"));
    const results = await Promise.all(requests);
    allProjects = results[0];
    if (results[1]) releaseStatus = results[1];
    updateReleaseBanner();
    updateMetrics(allProjects);
    filterProjects();
  } catch (err) {
    console.error("Failed to load projects:", err);
  }
}

// ---- Create Project Wizard Modal -----------------------------------
const wizardModal = document.getElementById("wizardModal");
const createVideoBtn = document.getElementById("createVideoBtn");
const emptyCreateBtn = document.getElementById("emptyCreateBtn");
const closeWizardBtn = document.getElementById("closeWizardBtn");
const cancelWizardBtn = document.getElementById("cancelWizardBtn");
const createProjectForm = document.getElementById("createProjectForm");
const submitCreateBtn = document.getElementById("submitCreateBtn");
const wizardOptionsStatus = document.getElementById("wizardOptionsStatus");
const retryWizardOptionsBtn = document.getElementById("retryWizardOptionsBtn");

let selectedPipeline = "screen-demo";
let selectedPlaybook = "premium-minimalist";
let wizardOptionsState = "idle";
let wizardOptionsRequest = null;

function setWizardOptionsStatus(message, state) {
  wizardOptionsStatus.textContent = message || "";
  wizardOptionsStatus.hidden = !message;
  wizardOptionsStatus.dataset.state = state;
  retryWizardOptionsBtn.hidden = state !== "error";
  submitCreateBtn.disabled = state !== "ready";
}

function normalizeWizardSelections() {
  const launchPipeline = availablePipelines.find((pipeline) => (
    pipeline && pipeline.creation_enabled === true
  ));
  if (!availablePipelines.some((pipeline) => (
    pipeline && pipeline.id === selectedPipeline && pipeline.creation_enabled === true
  ))) {
    selectedPipeline = launchPipeline?.id || "";
  }
  if (!availablePlaybooks.some((playbook) => playbook && playbook.id === selectedPlaybook)) {
    selectedPlaybook = availablePlaybooks[0]?.id || "";
  }
}

async function loadWizardOptions() {
  if (wizardOptionsState === "ready") return true;
  if (wizardOptionsRequest) return wizardOptionsRequest;

  wizardOptionsState = "loading";
  setWizardOptionsStatus("Loading the current production catalogs…", "loading");
  document.getElementById("pipelineSelectionGrid").innerHTML = '<div class="loading-spinner">Loading pipelines...</div>';
  document.getElementById("playbookSelectionGrid").innerHTML = '<div class="loading-spinner">Loading playbooks...</div>';

  wizardOptionsRequest = Promise.all([
    getJSON("/api/pipelines"),
    getJSON("/api/playbooks"),
    getJSON("/api/voices")
  ]).then(([pipelines, playbooks, voices]) => {
    const validPipelines = Array.isArray(pipelines)
      ? pipelines.filter((pipeline) => pipeline && typeof pipeline === "object" && typeof pipeline.id === "string")
      : [];
    const validPlaybooks = Array.isArray(playbooks)
      ? playbooks.filter((playbook) => playbook && typeof playbook === "object" && typeof playbook.id === "string")
      : [];
    const validVoices = Array.isArray(voices)
      ? voices.filter((voice) => voice && typeof voice === "object" && typeof voice.id === "string")
      : [];
    if (!validPipelines.some((pipeline) => pipeline.creation_enabled === true)) {
      throw new Error("No launch-enabled production pipeline is available");
    }
    if (validPlaybooks.length === 0) {
      throw new Error("No visual style playbook is available");
    }
    if (validVoices.length === 0) {
      throw new Error("No narration voice is available");
    }
    availablePipelines = validPipelines;
    availablePlaybooks = validPlaybooks;
    availableVoices = validVoices;
    normalizeWizardSelections();
    wizardOptionsState = "ready";
    setWizardOptionsStatus("", "ready");
    return true;
  }).catch((error) => {
    availablePipelines = [];
    availablePlaybooks = [];
    availableVoices = [];
    wizardOptionsState = "error";
    setWizardOptionsStatus("Current production options could not be loaded. Retry before creating a video.", "error");
    console.warn("Failed to load current production options:", error);
    return false;
  }).finally(() => {
    wizardOptionsRequest = null;
  });
  return wizardOptionsRequest;
}

async function openWizard() {
  wizardModal.style.display = "flex";
  wizardModal.setAttribute("aria-hidden", "false");
  document.getElementById("projectTitle").focus();

  await loadWizardOptions();

  renderPipelineOptions();
  renderPlaybookOptions();
  renderVoiceOptions();
}

function closeWizard() {
  wizardModal.style.display = "none";
  wizardModal.setAttribute("aria-hidden", "true");
  createProjectForm.reset();
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && wizardModal.style.display !== "none") {
    closeWizard();
  }
});

function renderPipelineOptions() {
  const container = document.getElementById("pipelineSelectionGrid");
  container.innerHTML = "";
  const pipelines = availablePipelines;
  if (pipelines.length === 0) {
    container.append(el("div", { class: "field-hint" }, "No production pipelines are available."));
    return;
  }

  for (const pipe of pipelines) {
    const lane = pipe.release_lane || "experimental";
    const canCreate = pipe.creation_enabled === true;
    const statusText = pipe.release_label || "Not production-certified";
    const selectPipeline = () => {
      if (!canCreate) return;
      selectedPipeline = pipe.id;
      renderPipelineOptions();
    };
    const card = el("div", { 
      class: `selector-card lane-${lane}${pipe.id === selectedPipeline ? " selected" : ""}${canCreate ? "" : " disabled"}`,
      role: "button",
      tabindex: canCreate ? "0" : "-1",
      "aria-disabled": canCreate ? "false" : "true",
      "aria-pressed": pipe.id === selectedPipeline ? "true" : "false",
      onclick: selectPipeline,
      onkeydown: (event) => {
        if (canCreate && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          selectPipeline();
        }
      }
    },
      el("div", { class: "selector-card-header" },
        el("span", { class: "selector-title" }, pipe.name),
        pipe.id === selectedPipeline ? el("span", { class: "selector-check" }, "✓") : null
      ),
      el("span", { class: `pipeline-release-label lane-${lane}` }, statusText),
      !canCreate && pipe.availability_reason
        ? el("span", { class: "selector-availability" }, pipe.availability_reason)
        : null,
      el("p", { class: "selector-desc" }, pipe.description || "Instruction-driven pipeline")
    );
    container.append(card);
  }
}

function renderPlaybookOptions() {
  const container = document.getElementById("playbookSelectionGrid");
  container.innerHTML = "";
  const playbooks = availablePlaybooks;
  if (playbooks.length === 0) {
    container.append(el("div", { class: "field-hint" }, "No visual style playbooks are available."));
    return;
  }

  for (const pb of playbooks) {
    const swatches = el("div", { class: "playbook-swatches" });
    const colors = [
      ...(pb.color_palette?.primary || ["#111827"]),
      ...(pb.color_palette?.accent || ["#3B82F6"])
    ].slice(0, 4);

    for (const c of colors) {
      swatches.append(el("span", { class: "swatch-dot", style: `background-color: ${c}` }));
    }

    const card = el("div", {
      class: `playbook-card${pb.id === selectedPlaybook ? " selected" : ""}`,
      role: "button",
      tabindex: "0",
      "aria-pressed": pb.id === selectedPlaybook ? "true" : "false",
      onclick: () => {
        selectedPlaybook = pb.id;
        renderPlaybookOptions();
      },
      onkeydown: (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectedPlaybook = pb.id;
          renderPlaybookOptions();
        }
      }
    },
      el("div", { class: "playbook-card-header" },
        el("span", { class: "playbook-title" }, pb.name),
        swatches
      ),
      el("span", { class: "playbook-mood" }, pb.mood || "Calibrated design system")
    );
    container.append(card);
  }
}

function renderVoiceOptions() {
  const select = document.getElementById("voiceSelect");
  select.innerHTML = "";
  const voices = availableVoices;
  select.disabled = voices.length === 0;
  if (voices.length === 0) {
    select.append(el("option", { value: "" }, "No narration voices are available"));
    return;
  }

  for (const v of voices) {
    const opt = el("option", { value: v.id }, v.name);
    select.append(opt);
  }
}

async function handleCreateProject(e) {
  if (e && e.preventDefault) e.preventDefault();
  const title = (document.getElementById("projectTitle")?.value || "").trim();
  const topic = (document.getElementById("projectTopic")?.value || "").trim();
  const voiceSelect = document.getElementById("voiceSelect");
  const voice = voiceSelect?.value || "en-US-ChristopherNeural";
  const durationSelect = document.getElementById("durationSelect");
  const duration = parseInt(durationSelect?.value || "30", 10);

  if (!title) {
    alert("Please enter a video title.");
    document.getElementById("projectTitle")?.focus();
    return;
  }

  // Keep the programmatic submit path aligned with the required textarea.
  // Native form validation runs for a normal browser submit, but this handler
  // is also called directly by the click listener and by keyboard submits.
  // Without this guard a title-only request silently falls back to using the
  // title as the topic, producing an under-specified creative brief.
  if (!topic) {
    alert("Please describe the video topic and key takeaways.");
    document.getElementById("projectTopic")?.focus();
    return;
  }

  if (wizardOptionsState !== "ready") {
    alert("Current production options are not ready. Retry loading them before creating a video.");
    return;
  }

  submitCreateBtn.disabled = true;
  submitCreateBtn.innerHTML = `<span>Creating internal preview...</span>`;

  try {
    const res = await fetch("/api/project/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: title,
        topic_prompt: topic,
        pipeline_type: selectedPipeline || "animated-explainer",
        playbook: selectedPlaybook || "premium-minimalist",
        voice: voice,
        target_duration_seconds: duration
      })
    });
    const data = await res.json();
    if (data.ok && data.project_id) {
      // Trigger background pipeline run immediately
      let runError = null;
      try {
        const runResponse = await fetch(`/api/project/${data.project_id}/run`, { method: "POST" });
        const runData = await runResponse.json().catch(() => ({}));
        if (!runResponse.ok || runData.ok !== true) {
          throw new Error(runData.detail || runData.error || `HTTP ${runResponse.status}`);
        }
      } catch (runErr) {
        runError = runErr;
        console.error("Auto-run trigger failed:", runErr);
      }
      if (runError) {
        const detail = String(runError.message || runError).slice(0, 300);
        alert(`Project created, but automatic run could not start: ${detail}. Open the project to retry.`);
      }
      window.location.href = `/p/${data.project_id}`;
    } else {
      alert("Error creating project: " + (data.detail || data.error || "Unknown error"));
      submitCreateBtn.disabled = false;
      submitCreateBtn.innerHTML = `<span>Create Internal Preview</span>`;
    }
  } catch (err) {
    console.error("Create project failed:", err);
    alert("Failed to initialize project.");
    submitCreateBtn.disabled = false;
    submitCreateBtn.innerHTML = `<span>Create Internal Preview</span>`;
  }
}

// ---- Event Listeners -----------------------------------------------
createVideoBtn.addEventListener("click", openWizard);
if (emptyCreateBtn) emptyCreateBtn.addEventListener("click", openWizard);
closeWizardBtn.addEventListener("click", closeWizard);
cancelWizardBtn.addEventListener("click", closeWizard);
retryWizardOptionsBtn.addEventListener("click", async () => {
  await loadWizardOptions();
  renderPipelineOptions();
  renderPlaybookOptions();
  renderVoiceOptions();
});
createProjectForm.addEventListener("submit", handleCreateProject);
submitCreateBtn.addEventListener("click", (e) => {
  if (createProjectForm.checkValidity && !createProjectForm.checkValidity()) {
    createProjectForm.reportValidity();
    return;
  }
  handleCreateProject(e);
});

document.getElementById("refreshBtn").addEventListener("click", refreshLibrary);

projectSearch.addEventListener("input", (e) => {
  searchQuery = e.target.value;
  filterProjects();
});

filterTabs.addEventListener("click", (e) => {
  const btn = e.target.closest(".tab-btn");
  if (!btn) return;
  filterTabs.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  currentFilter = btn.dataset.filter;
  filterProjects();
});

// Initial boot
refreshLibrary().catch(console.error);

// Real-time SSE event subscriptions
if (!new URLSearchParams(location.search).has("static")) {
  subscribe("/api/library/events", () => refreshLibrary().catch(console.error));
}
