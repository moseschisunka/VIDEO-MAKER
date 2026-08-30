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
    poster.append(el("span", { class: "lp-badge badge-emerald" }, "✓ DELIVERABLE READY"));
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

  const metaChips = el("div", { class: "card-meta-chips" },
    el("span", { class: "meta-chip chip-pipeline" }, p.pipeline_type || "animated-explainer"),
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
    allProjects = await getJSON("/api/projects");
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

let selectedPipeline = "animated-explainer";
let selectedPlaybook = "premium-minimalist";

async function openWizard() {
  wizardModal.style.display = "flex";
  document.getElementById("projectTitle").focus();

  // Load pipelines, playbooks, voices if not loaded
  if (availablePipelines.length === 0) {
    try {
      document.getElementById('pipelineSelectionGrid').innerHTML = '<div class="loading-spinner">Loading pipelines...</div>';
      document.getElementById('playbookSelectionGrid').innerHTML = '<div class="loading-spinner">Loading playbooks...</div>';
      [availablePipelines, availablePlaybooks, availableVoices] = await Promise.all([
        getJSON("/api/pipelines"),
        getJSON("/api/playbooks"),
        getJSON("/api/voices")
      ]);
    } catch (e) {
      console.warn("Failed to load options:", e);
    }
  }

  renderPipelineOptions();
  renderPlaybookOptions();
  renderVoiceOptions();
}

function closeWizard() {
  wizardModal.style.display = "none";
  createProjectForm.reset();
}

function renderPipelineOptions() {
  const container = document.getElementById("pipelineSelectionGrid");
  container.innerHTML = "";
  const pipelines = availablePipelines.length ? availablePipelines : [
    { id: "animated-explainer", name: "Animated Explainer", description: "React Remotion motion cards, charts & narration" },
    { id: "cinematic", name: "Cinematic Montage", description: "Atmospheric camera movement, grading & score" },
    { id: "screen-demo", name: "Screen Demo", description: "Product walkthrough with zooms and cursor focus" },
    { id: "character-animation", name: "Character Animation", description: "Rigged vector character animation with lip-sync" },
  ];

  for (const pipe of pipelines) {
    const card = el("div", { 
      class: `selector-card${pipe.id === selectedPipeline ? " selected" : ""}`,
      onclick: () => {
        selectedPipeline = pipe.id;
        renderPipelineOptions();
      }
    },
      el("div", { class: "selector-card-header" },
        el("span", { class: "selector-title" }, pipe.name),
        pipe.id === selectedPipeline ? el("span", { class: "selector-check" }, "✓") : null
      ),
      el("p", { class: "selector-desc" }, pipe.description || "Instruction-driven pipeline")
    );
    container.append(card);
  }
}

function renderPlaybookOptions() {
  const container = document.getElementById("playbookSelectionGrid");
  container.innerHTML = "";
  const playbooks = availablePlaybooks.length ? availablePlaybooks : [
    { id: "premium-minimalist", name: "Premium Minimalist", mood: "Editorial, High Trust", color_palette: { primary: ["#111827"], accent: ["#2563EB"] } },
    { id: "anime-ghibli", name: "Anime Ghibli", mood: "Hand-painted, Warm Aesthetic", color_palette: { primary: ["#3D4A3E"], accent: ["#D97706"] } },
    { id: "flat-motion-graphics", name: "Flat Motion Graphics", mood: "Bold Geometry, Vibrant", color_palette: { primary: ["#0F172A"], accent: ["#00E5FF"] } },
    { id: "minimalist-diagram", name: "Minimalist Diagram", mood: "Technical & Schematic", color_palette: { primary: ["#000000"], accent: ["#10B981"] } },
  ];

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
      onclick: () => {
        selectedPlaybook = pb.id;
        renderPlaybookOptions();
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
  const voices = availableVoices.length ? availableVoices : [
    { id: "en-US-ChristopherNeural", name: "Christopher (US Male - Authoritative & Warm)" },
    { id: "en-US-AriaNeural", name: "Aria (US Female - Clear & Dynamic)" },
    { id: "en-US-GuyNeural", name: "Guy (US Male - Casual & Friendly)" },
    { id: "en-US-JennyNeural", name: "Jenny (US Female - Natural Explainer)" },
    { id: "en-GB-RyanNeural", name: "Ryan (UK Male - Polished British)" },
  ];

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

  submitCreateBtn.disabled = true;
  submitCreateBtn.innerHTML = `<span>Initializing Studio & Pipeline...</span>`;

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
      try {
        await fetch(`/api/project/${data.project_id}/run`, { method: "POST" });
      } catch (runErr) {
        console.warn("Auto-run trigger:", runErr);
      }
      window.location.href = `/p/${data.project_id}`;
    } else {
      alert("Error creating project: " + (data.error || "Unknown error"));
      submitCreateBtn.disabled = false;
      submitCreateBtn.innerHTML = `<span>Initialize & Launch Video</span>`;
    }
  } catch (err) {
    console.error("Create project failed:", err);
    alert("Failed to initialize project.");
    submitCreateBtn.disabled = false;
    submitCreateBtn.innerHTML = `<span>Initialize & Launch Video</span>`;
  }
}

// ---- Event Listeners -----------------------------------------------
createVideoBtn.addEventListener("click", openWizard);
if (emptyCreateBtn) emptyCreateBtn.addEventListener("click", openWizard);
closeWizardBtn.addEventListener("click", closeWizard);
cancelWizardBtn.addEventListener("click", closeWizard);
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

