"use strict";

const hostedApiHosts = new Set(["joachim2018.github.io", "modular-b0ay.onrender.com"]);

window.MODULAR_API_BASE_URL = hostedApiHosts.has(window.location.hostname)
  ? "https://modular-b0ay.onrender.com"
  : "";
