"use strict";

const apiBaseUrl = typeof window.MODULAR_API_BASE_URL === "string"
  ? window.MODULAR_API_BASE_URL.trim().replace(/\/+$/, "")
  : "";
const localApiHosts = new Set(["localhost", "127.0.0.1"]);

function apiUrl(path) {
  return `${apiBaseUrl}${path}`;
}

function hasApiTarget() {
  return Boolean(apiBaseUrl) || localApiHosts.has(window.location.hostname);
}

function fetchApi(path, options = {}, timeout = 45000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  return fetch(apiUrl(path), { ...options, signal: controller.signal }).finally(() => clearTimeout(timeoutId));
}

const form = document.querySelector("#planForm");
const courseInput = document.querySelector("#course");
const modulesInput = document.querySelector("#modules");
const formAlert = document.querySelector("#formAlert");
const generateButton = document.querySelector("#generateButton");
const generateSpinner = document.querySelector("#generateSpinner");
const moduleCount = document.querySelector("#moduleCount");
const resultsSection = document.querySelector("#results");
const opportunityCourse = document.querySelector("#opportunityCourse");
const opportunityAlert = document.querySelector("#opportunitiesAlert");
const opportunityCards = document.querySelector("#opportunityCards");
const linkedinSearchLink = document.querySelector("#linkedinSearchLink");
let currentPlan = null;
let moduleChart = null;

const allowedText = /^[\p{L}\p{N}\s&.,()/'’:+#-]+$/u;

function parseModules(value) {
  return value.split(/[\n,;]+/).map((part) => part.trim().replace(/\s+/g, " ")).filter(Boolean);
}

function validateInput(course, modules) {
  if (!course || course.length > 100 || !allowedText.test(course)) {
    return "Enter a course name using letters, numbers, and common punctuation (up to 100 characters).";
  }
  if (modules.length < 2 || modules.length > 12) {
    return "Add between 2 and 12 modules.";
  }
  if (modules.some((item) => item.length > 80 || !allowedText.test(item))) {
    return "Each module must be 80 characters or fewer and use letters, numbers, and common punctuation.";
  }
  if (new Set(modules.map((item) => item.toLocaleLowerCase())).size !== modules.length) {
    return "Remove duplicate module names so each topic gets its own resources.";
  }
  return "";
}

function setAlert(element, message, visible = true) {
  element.textContent = message;
  element.hidden = !visible;
}

async function readApiJson(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new Error("The API returned a web page instead of JSON. Check the API URL in api-config.js and confirm the Python service is running.");
  }
  try {
    return await response.json();
  } catch {
    throw new Error("The server returned invalid JSON. Check the server and try again.");
  }
}

function setLoading(loading) {
  generateButton.disabled = loading;
  generateSpinner.classList.toggle("d-none", !loading);
  generateButton.querySelector(".button-label").textContent = loading ? "Building your guide…" : "Generate my learning plan";
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function createResource(resource) {
  const item = document.createElement("li");
  const link = document.createElement("a");
  const safeUrl = safeExternalUrl(resource.url);
  link.textContent = resource.title || "Open resource";
  if (safeUrl) {
    link.href = safeUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  } else {
    link.removeAttribute("href");
  }
  const type = document.createElement("span");
  type.className = "resource-type";
  type.textContent = resource.type || "Resource";
  item.append(link, type);
  return item;
}

function renderPlan(plan) {
  currentPlan = plan;
  document.querySelector("#resultsTitle").textContent = `${plan.course} study guide`;
  document.querySelector("#resultsSubtitle").textContent = `${plan.modules.length} modules · generated ${new Date(plan.generatedAt).toLocaleDateString()}`;
  const cards = document.querySelector("#moduleCards");
  cards.replaceChildren();

  plan.modules.forEach((module) => {
    const column = document.createElement("div");
    column.className = "col-md-6 col-xl-4";
    const card = document.createElement("article");
    card.className = "module-card";
    const top = document.createElement("div");
    top.className = "module-card-top";
    const title = document.createElement("h3");
    title.textContent = module.name;
    const chip = document.createElement("span");
    chip.className = "difficulty-chip";
    chip.textContent = `Difficulty ${module.difficulty}/10`;
    top.append(title, chip);
    const summary = document.createElement("p");
    summary.className = "module-summary";
    summary.textContent = module.summary;
    const list = document.createElement("ul");
    list.className = "resource-list";
    module.resources.forEach((resource) => list.append(createResource(resource)));
    card.append(top, summary, list);
    column.append(card);
    cards.append(column);
  });

  if (moduleChart) moduleChart.destroy();
  const context = document.querySelector("#moduleChart");
  moduleChart = new Chart(context, {
    type: "bar",
    data: {
      labels: plan.modules.map((module) => module.name),
      datasets: [
        { label: "Complexity", data: plan.modules.map((module) => module.complexity), backgroundColor: "#becf67", borderRadius: 4, maxBarThickness: 30 },
        { label: "Difficulty", data: plan.modules.map((module) => module.difficulty), backgroundColor: "#9cb2ce", borderRadius: 4, maxBarThickness: 30 }
      ]
    },
    options: {
      maintainAspectRatio: false,
      responsive: true,
      scales: {
        y: { min: 0, max: 10, ticks: { stepSize: 2, color: "#8791a0" }, grid: { color: "#e8e9e4" }, border: { display: false } },
        x: { ticks: { color: "#536076", maxRotation: 35, minRotation: 0 }, grid: { display: false }, border: { display: false } }
      },
      plugins: { legend: { position: "bottom", labels: { usePointStyle: true, pointStyle: "circle", color: "#586579", padding: 20, font: { family: "DM Sans", size: 11 } } } }
    }
  });
  resultsSection.hidden = false;
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const course = courseInput.value.trim().replace(/\s+/g, " ");
  const modules = parseModules(modulesInput.value);
  const validationMessage = validateInput(course, modules);
  setAlert(formAlert, validationMessage, Boolean(validationMessage));
  courseInput.classList.toggle("is-invalid", !course || course.length > 100 || !allowedText.test(course));
  if (validationMessage) return;
  if (!hasApiTarget()) {
    setAlert(formAlert, "The hosted API URL is not configured. Set MODULAR_API_BASE_URL in api-config.js and redeploy the site.");
    return;
  }

  setLoading(true);
  try {
    generateButton.querySelector(".button-label").textContent = "Connecting to API…";
    const statusResponse = await fetchApi("/api/status", {}, 120000);
    const providerStatus = await readApiJson(statusResponse);
    if (!statusResponse.ok || !providerStatus.openai) {
      throw new Error("OpenAI is not configured on the API service. Add OPENAI_API_KEY to the Render environment and redeploy.");
    }

    generateButton.querySelector(".button-label").textContent = "Building your guide…";
    const response = await fetchApi("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course, modules })
    });
    const payload = await readApiJson(response);
    if (!response.ok) throw new Error(payload.error || "The learning plan could not be generated.");
    renderPlan(payload);
  } catch (error) {
    const message = error.name === "AbortError"
      ? "The API request timed out. A free-tier API service may still be starting; wait a moment and try again."
      : error.message || "Unable to connect to Modular. Check the server and try again.";
    setAlert(formAlert, message);
  } finally {
    setLoading(false);
  }
});

modulesInput.addEventListener("input", () => {
  const count = parseModules(modulesInput.value).length;
  moduleCount.textContent = `${count} / 12`;
});

document.querySelector("#printButton").addEventListener("click", () => window.print());

document.querySelector("#downloadButton").addEventListener("click", () => {
  if (!currentPlan) return;
  const file = new Blob([JSON.stringify(currentPlan, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${currentPlan.course.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-learning-plan.json`;
  link.click();
  URL.revokeObjectURL(url);
});

async function findOpportunities() {
  const course = opportunityCourse.value.trim().replace(/\s+/g, " ");
  if (!course || course.length > 100 || !allowedText.test(course)) {
    setAlert(opportunityAlert, "Enter a course name using letters, numbers, and common punctuation.");
    opportunityCourse.focus();
    return;
  }
  linkedinSearchLink.href = `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(course)}`;
  if (!hasApiTarget()) {
    setAlert(opportunityAlert, "The hosted API URL is not configured. Set MODULAR_API_BASE_URL in api-config.js to enable job search.");
    return;
  }
  setAlert(opportunityAlert, "Searching configured job sources…");
  opportunityCards.replaceChildren();
  try {
    const response = await fetchApi(`/api/jobs?course=${encodeURIComponent(course)}`);
    const payload = await readApiJson(response);
    if (!response.ok) throw new Error(payload.error || "Job listings are not available.");
    if (!payload.jobs.length) {
      setAlert(opportunityAlert, "No matching jobs were returned. Try a broader course name or search directly on LinkedIn.");
      return;
    }
    setAlert(opportunityAlert, `${payload.jobs.length} opportunities found.`, true);
    payload.jobs.forEach((job) => {
      const column = document.createElement("div");
      column.className = "col-md-6 col-lg-4";
      const card = document.createElement("article");
      card.className = "job-card";
      const title = document.createElement("h3");
      title.textContent = job.title;
      const details = document.createElement("p");
      details.textContent = [job.company, job.location].filter(Boolean).join(" · ") || "Opportunity";
      const link = document.createElement("a");
      const safeUrl = safeExternalUrl(job.url);
      if (safeUrl) {
        link.href = safeUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "View opportunity →";
      } else {
        link.textContent = "Listing link unavailable";
      }
      card.append(title, details, link);
      column.append(card);
      opportunityCards.append(column);
    });
  } catch (error) {
    setAlert(opportunityAlert, `${error.message} You can still search the course on LinkedIn.`);
  }
}

document.querySelector("#findJobsButton").addEventListener("click", findOpportunities);
opportunityCourse.addEventListener("keydown", (event) => {
  if (event.key === "Enter") findOpportunities();
});
opportunityCourse.addEventListener("input", () => {
  linkedinSearchLink.href = `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(opportunityCourse.value.trim())}`;
});

async function restoreLastPlan() {
  if (apiBaseUrl) return;
  try {
    const response = await fetchApi("/api/output");
    if (response.status === 404) return;
    if (!response.ok) throw new Error("The saved learning plan could not be loaded.");
    const plan = await response.json();
    if (plan && Array.isArray(plan.modules) && plan.modules.length) renderPlan(plan);
  } catch (error) {
    setAlert(formAlert, error.message || "The saved learning plan could not be loaded.");
  }
}

restoreLastPlan();
