const state = {
  packages: [],
  editingPackageId: null,
  styleRefs: [],
  latestDebugBundle: null,
  runtimeConfig: null,
  speech: {
    recognition: null,
    listening: false,
  },
};

const API_BASE = window.STORYBUDDY_API_BASE || "";

const el = {
  tabs: Array.from(document.querySelectorAll(".tab")),
  panels: {
    setup: document.getElementById("setup-panel"),
    ask: document.getElementById("ask-panel"),
    library: document.getElementById("library-panel"),
  },
  storyTitle: document.getElementById("storyTitle"),
  bookFile: document.getElementById("bookFile"),
  bookText: document.getElementById("bookText"),
  styleRefs: document.getElementById("styleRefs"),
  learnBtn: document.getElementById("learnBtn"),
  savePackageBtn: document.getElementById("savePackageBtn"),
  resetSetupBtn: document.getElementById("resetSetupBtn"),
  learnSummary: document.getElementById("learnSummary"),

  packageSelect: document.getElementById("packageSelect"),
  modelSelect: document.getElementById("modelSelect"),
  questionInput: document.getElementById("questionInput"),
  speechBtn: document.getElementById("speechBtn"),
  speechStatus: document.getElementById("speechStatus"),
  generateBtn: document.getElementById("generateBtn"),
  clearRunBtn: document.getElementById("clearRunBtn"),
  timingsView: document.getElementById("timingsView"),
  timelineView: document.getElementById("timelineView"),
  cards: document.getElementById("cards"),
  debugView: document.getElementById("debugView"),
  copyDebugBtn: document.getElementById("copyDebugBtn"),

  refreshLibraryBtn: document.getElementById("refreshLibraryBtn"),
  libraryList: document.getElementById("libraryList"),
  libraryItemTemplate: document.getElementById("libraryItemTemplate"),
  cardTemplate: document.getElementById("cardTemplate"),
};

init().catch((err) => {
  el.learnSummary.textContent = `Initialization failed: ${err.message}`;
});

async function init() {
  await loadRuntimeConfig();
  wireTabs();
  wireSetup();
  wireAsk();
  wireLibrary();
  setupSpeech();
  await refreshPackages();
  clearRun();
  if (state.runtimeConfig?.imageProvider === "mock") {
    setSetupNote("Image provider is currently mock. Configure STORYBUDDY_IMAGE_PROVIDER and STORYBUDDY_IMAGE_API_KEY for real model generation.");
  }
}

async function loadRuntimeConfig() {
  try {
    state.runtimeConfig = await apiRequest("/api/config");
  } catch {
    state.runtimeConfig = null;
  }
}

function wireTabs() {
  el.tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const tabName = tab.dataset.tab;
      el.tabs.forEach((entry) => {
        const active = entry === tab;
        entry.classList.toggle("is-active", active);
        entry.setAttribute("aria-selected", active ? "true" : "false");
      });

      Object.entries(el.panels).forEach(([name, panel]) => {
        const active = name === tabName;
        panel.classList.toggle("is-active", active);
        panel.setAttribute("aria-hidden", active ? "false" : "true");
      });
    });
  });
}

function wireSetup() {
  el.bookFile.addEventListener("change", async () => {
    const file = el.bookFile.files?.[0];
    if (!file) {
      return;
    }

    const isText = /text|json|markdown/.test(file.type) || /\.(txt|md|json)$/i.test(file.name);
    const isPdf = /\.pdf$/i.test(file.name) || file.type === "application/pdf";

    if (isText) {
      el.bookText.value = await file.text();
      setSetupNote("Loaded text file content.");
      return;
    }

    if (isPdf) {
      setSetupNote("PDF selected. Extracting text...");
      try {
        const extraction = await extractPdfTextFromFile(file);
        if (extraction.text && extraction.text.length >= 40) {
          el.bookText.value = extraction.text;
          if (!el.storyTitle.value.trim()) {
            el.storyTitle.value = file.name.replace(/\.pdf$/i, "");
          }
          setSetupNote(
            `Extracted text from PDF (${extraction.method}, ${extraction.pageCount} page${
              extraction.pageCount === 1 ? "" : "s"
            }).`
          );
        } else {
          setSetupNote("PDF selected, but text extraction was too short. You can still click Save to try server extraction.");
        }
      } catch (err) {
        setSetupNote(`PDF extraction failed in browser (${readError(err)}). You can still click Save to try server extraction.`);
      }
      return;
    }

    setSetupNote("Unsupported file type. Use PDF or text file.");
  });

  el.styleRefs.addEventListener("change", async () => {
    const files = Array.from(el.styleRefs.files || []);
    state.styleRefs = await Promise.all(
      files.map(async (file, idx) => ({
        id: `style-${Date.now()}-${idx}`,
        name: file.name,
        dataUrl: await fileToDataUrl(file),
      }))
    );

    setSetupNote(`Loaded ${state.styleRefs.length} style reference image${state.styleRefs.length === 1 ? "" : "s"}.`);
  });

  el.learnBtn.addEventListener("click", () => {
    const summary = localAnalyze(el.bookText.value || "");
    if (!summary) {
      setSetupNote("Add story text first, or upload a PDF and click Save.");
      return;
    }

    setSetupNote(
      `Preview: ${summary.facts} facts, ${summary.characters} characters, ${summary.objects} objects, ${summary.scenes} scenes.`
    );
  });

  el.savePackageBtn.addEventListener("click", async () => {
    const storyTitle = el.storyTitle.value.trim();
    if (!storyTitle) {
      setSetupNote("Please enter a story title.");
      return;
    }

    el.savePackageBtn.disabled = true;
    const original = el.savePackageBtn.textContent;
    el.savePackageBtn.textContent = "Saving...";

    try {
      const file = el.bookFile.files?.[0];
      const isPdf = file && (/\.pdf$/i.test(file.name) || file.type === "application/pdf");
      const styleRefs = state.styleRefs.length ? state.styleRefs : (currentEditingPackage()?.style_refs || []);

      const payload = {
        packageId: state.editingPackageId,
        storyTitle,
        bookText: el.bookText.value.trim() || null,
        pdfBase64: isPdf && !el.bookText.value.trim() ? await fileToDataUrl(file) : null,
        styleRefs,
        characterImageHints: buildCharacterImageHints(el.bookText.value, styleRefs),
      };

      const result = await apiRequest("/api/setup/ingest", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      state.editingPackageId = result.package.id;
      setSetupNote(
        `Saved ${result.package.title}. Learned ${result.learnedSummary.facts} facts, ${result.learnedSummary.characters} characters, ${result.learnedSummary.characterMappings} character-image mappings.`
      );

      await refreshPackages(result.package.id);
    } catch (err) {
      setSetupNote(`Save failed: ${readError(err)}`);
    } finally {
      el.savePackageBtn.disabled = false;
      el.savePackageBtn.textContent = original;
    }
  });

  el.resetSetupBtn.addEventListener("click", () => {
    resetSetup();
    setSetupNote("Setup form reset.");
  });
}

function wireAsk() {
  el.generateBtn.addEventListener("click", async () => {
    const packageId = el.packageSelect.value;
    const question = el.questionInput.value.trim();
    const model = el.modelSelect.value;
    const selectedPackage = state.packages.find((pkg) => pkg.id === packageId) || null;

    if (!packageId) {
      el.timingsView.textContent = "Select a story package first.";
      return;
    }
    if (!question) {
      el.timingsView.textContent = "Enter or dictate a question first.";
      return;
    }

    el.generateBtn.disabled = true;
    el.generateBtn.textContent = "Generating...";

    try {
      const result = await apiRequest("/api/ask", {
        method: "POST",
        body: JSON.stringify({ packageId, package: selectedPackage, question, model }),
      });
      state.latestDebugBundle = result.debugBundle;
      renderAskResult(result);
    } catch (err) {
      el.timingsView.textContent = `Ask failed: ${readError(err)}`;
    } finally {
      el.generateBtn.disabled = false;
      el.generateBtn.textContent = "Generate 3 Answer Cards";
    }
  });

  el.clearRunBtn.addEventListener("click", clearRun);

  el.copyDebugBtn.addEventListener("click", async () => {
    if (!state.latestDebugBundle) {
      return;
    }
    try {
      await navigator.clipboard.writeText(JSON.stringify(state.latestDebugBundle, null, 2));
      el.copyDebugBtn.textContent = "Copied";
    } catch {
      el.copyDebugBtn.textContent = "Copy failed";
    }
    setTimeout(() => {
      el.copyDebugBtn.textContent = "Copy JSON";
    }, 1200);
  });
}

function wireLibrary() {
  el.refreshLibraryBtn.addEventListener("click", () => refreshPackages());
}

function setupSpeech() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    el.speechBtn.disabled = true;
    el.speechStatus.textContent = "Speech API unavailable in this browser";
    return;
  }

  const recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.addEventListener("start", () => {
    state.speech.listening = true;
    el.speechStatus.textContent = "Listening...";
    el.speechBtn.textContent = "Stop voice capture";
  });

  recognition.addEventListener("end", () => {
    state.speech.listening = false;
    el.speechStatus.textContent = "Idle";
    el.speechBtn.textContent = "Start voice capture";
  });

  recognition.addEventListener("result", (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript?.trim();
    if (transcript) {
      el.questionInput.value = transcript;
      el.speechStatus.textContent = "Captured";
    }
  });

  recognition.addEventListener("error", (event) => {
    el.speechStatus.textContent = `Speech error: ${event.error}`;
  });

  state.speech.recognition = recognition;
  el.speechBtn.addEventListener("click", () => {
    if (!state.speech.recognition) {
      return;
    }
    if (state.speech.listening) {
      state.speech.recognition.stop();
    } else {
      state.speech.recognition.start();
    }
  });
}

async function refreshPackages(preferredId = "") {
  const packages = await apiRequest("/api/packages");
  state.packages = Array.isArray(packages) ? packages : [];
  renderPackageSelect(preferredId);
  renderLibrary();
}

function renderPackageSelect(preferredId = "") {
  el.packageSelect.innerHTML = "";
  if (!state.packages.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No story package yet";
    el.packageSelect.appendChild(option);
    return;
  }

  state.packages.forEach((pkg, idx) => {
    const option = document.createElement("option");
    option.value = pkg.id;
    option.textContent = `${pkg.title} (${(pkg.characters || []).length} chars)`;
    if ((preferredId && pkg.id === preferredId) || (!preferredId && idx === 0)) {
      option.selected = true;
    }
    el.packageSelect.appendChild(option);
  });
}

function renderLibrary() {
  el.libraryList.innerHTML = "";
  if (!state.packages.length) {
    const p = document.createElement("p");
    p.textContent = "No packages yet. Create one in Setup.";
    el.libraryList.appendChild(p);
    return;
  }

  state.packages.forEach((pkg) => {
    const node = el.libraryItemTemplate.content.firstElementChild.cloneNode(true);
    node.querySelector(".libraryItem__title").textContent = pkg.title;
    node.querySelector(".libraryItem__meta").textContent =
      `Facts: ${(pkg.facts || []).length} | Characters: ${(pkg.characters || []).length} | Refs: ${(pkg.style_refs || []).length} | Updated: ${new Date(pkg.updated_at).toLocaleString()}`;

    node.querySelector(".js-open").addEventListener("click", () => {
      loadPackageToSetup(pkg.id);
      activateTab("setup");
    });

    node.querySelector(".js-select").addEventListener("click", () => {
      renderPackageSelect(pkg.id);
      activateTab("ask");
    });

    node.querySelector(".js-delete").addEventListener("click", async () => {
      try {
        await apiRequest(`/api/packages/${pkg.id}`, { method: "DELETE" });
        if (state.editingPackageId === pkg.id) {
          resetSetup();
        }
        await refreshPackages();
      } catch (err) {
        setSetupNote(`Delete failed: ${readError(err)}`);
      }
    });

    el.libraryList.appendChild(node);
  });
}

function loadPackageToSetup(packageId) {
  const pkg = state.packages.find((entry) => entry.id === packageId);
  if (!pkg) {
    return;
  }

  state.editingPackageId = pkg.id;
  state.styleRefs = pkg.style_refs || [];
  el.storyTitle.value = pkg.title || "";
  el.bookText.value = pkg.raw_text || "";
  el.bookFile.value = "";
  setSetupNote(
    `Loaded ${pkg.title}. Character-image mappings: ${(pkg.character_style_map || []).filter((m) => (m.ref_ids || []).length).length}`
  );
}

function clearRun() {
  el.timingsView.textContent = "No run yet.";
  el.timelineView.textContent = "No run yet.";
  el.debugView.textContent = "No run yet.";
  el.cards.innerHTML = "";
  state.latestDebugBundle = null;
}

function renderAskResult(result) {
  const stepTimings = result.telemetry?.stepTimings || result.telemetry?.step_timings || {};
  el.timingsView.textContent = JSON.stringify(stepTimings, null, 2);

  const timeline = result.telemetry?.timeline || [];
  el.timelineView.textContent = timeline
    .map((entry) => `${entry.lane.padEnd(7)} | +${String(entry.startMs).padStart(5)}ms | ${String(entry.durationMs).padStart(5)}ms | ${entry.event}`)
    .join("\n");

  el.cards.innerHTML = "";
  (result.cards || []).forEach((card) => {
    const node = el.cardTemplate.content.firstElementChild.cloneNode(true);
    const image = node.querySelector(".card__media");
    const text = node.querySelector(".card__text");
    const badge = node.querySelector(".card__badge");

    image.src = card.imageDataUrl;
    text.textContent = card.text;
    badge.textContent = "Tap to select";

    node.addEventListener("click", () => {
      Array.from(el.cards.children).forEach((cardNode) => {
        cardNode.querySelector(".card__badge").textContent = "Tap to select";
      });
      badge.textContent = card.isCorrect ? "Selected (book-supported answer)" : "Selected";
    });

    el.cards.appendChild(node);
  });

  el.debugView.textContent = JSON.stringify(result.debugBundle || {}, null, 2);
}

function currentEditingPackage() {
  return state.packages.find((pkg) => pkg.id === state.editingPackageId);
}

function resetSetup() {
  state.editingPackageId = null;
  state.styleRefs = [];
  el.storyTitle.value = "";
  el.bookText.value = "";
  el.bookFile.value = "";
  el.styleRefs.value = "";
}

function activateTab(name) {
  const tab = el.tabs.find((entry) => entry.dataset.tab === name);
  if (tab) {
    tab.click();
  }
}

function setSetupNote(message) {
  el.learnSummary.textContent = message;
}

function localAnalyze(text) {
  const cleaned = (text || "").replace(/\s+/g, " ").trim();
  if (!cleaned) {
    return null;
  }
  const sentences = cleaned.split(/(?<=[.!?])\s+/).filter(Boolean);
  const chars = (cleaned.match(/\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b/g) || []).slice(0, 12);
  const objects = (cleaned.toLowerCase().match(/[a-z]{4,}/g) || []).slice(0, 50);
  return {
    facts: sentences.slice(0, 24).length,
    characters: new Set(chars).size,
    objects: new Set(objects).size,
    scenes: sentences.filter((s) => /\b(in|at|on|near|inside|outside|by)\b/i.test(s)).length || 1,
  };
}

function buildCharacterImageHints(text, styleRefs) {
  const matches = text.match(/\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b/g) || [];
  const characters = Array.from(new Set(matches)).filter((name) => !/^(The|A|An|And|But|When|Then)$/.test(name));
  const hints = {};

  characters.forEach((character) => {
    const tokens = character.toLowerCase().split(/\s+/);
    const ids = styleRefs
      .filter((ref) => tokens.some((tok) => ref.name.toLowerCase().includes(tok)))
      .map((ref) => ref.id)
      .slice(0, 2);
    if (ids.length) {
      hints[character] = ids;
    }
  });

  return hints;
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const raw = await response.text();
  let body = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = { raw };
    }
  }

  if (!response.ok) {
    const message = body?.detail || body?.error || `${response.status} ${response.statusText}`;
    throw new Error(message);
  }

  return body;
}

function readError(err) {
  return err instanceof Error ? err.message : String(err);
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Failed to read file"));
    reader.readAsDataURL(file);
  });
}

async function extractPdfTextFromFile(file) {
  const arrayBuffer = await file.arrayBuffer();

  try {
    const pdfJs = await import("https://cdn.jsdelivr.net/npm/pdfjs-dist@4.6.82/build/pdf.min.mjs");
    if (pdfJs.GlobalWorkerOptions) {
      pdfJs.GlobalWorkerOptions.workerSrc =
        "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.6.82/build/pdf.worker.min.mjs";
    }

    const doc = await pdfJs.getDocument({ data: arrayBuffer }).promise;
    const pages = [];
    for (let pageNumber = 1; pageNumber <= doc.numPages; pageNumber += 1) {
      const page = await doc.getPage(pageNumber);
      const textContent = await page.getTextContent();
      const pageText = textContent.items
        .map((item) => (typeof item.str === "string" ? item.str : ""))
        .join(" ");
      pages.push(`[Page ${pageNumber}] ${pageText}`);
    }

    return {
      text: cleanExtractedText(pages.join("\n\n")),
      pageCount: doc.numPages,
      method: "pdf.js",
    };
  } catch {
    return {
      text: extractPdfTextHeuristic(arrayBuffer),
      pageCount: 1,
      method: "heuristic",
    };
  }
}

function extractPdfTextHeuristic(arrayBuffer) {
  const source = new TextDecoder("latin1").decode(new Uint8Array(arrayBuffer));
  const chunks = [];

  const simpleTextOps = source.matchAll(/\(([^()]{2,260})\)\s*Tj/g);
  for (const match of simpleTextOps) {
    chunks.push(decodePdfLiteralString(match[1]));
  }

  const arrayTextOps = source.matchAll(/\[(.*?)\]\s*TJ/gs);
  for (const match of arrayTextOps) {
    const inner = match[1].matchAll(/\(([^()]*)\)/g);
    for (const part of inner) {
      chunks.push(decodePdfLiteralString(part[1]));
    }
  }

  return cleanExtractedText(chunks.join(" "));
}

function decodePdfLiteralString(input) {
  return input
    .replace(/\\([nrtbf()\\])/g, (_, token) => {
      const map = {
        n: "\n",
        r: "\r",
        t: "\t",
        b: "\b",
        f: "\f",
        "(": "(",
        ")": ")",
        "\\": "\\",
      };
      return map[token] || token;
    })
    .replace(/\\([0-7]{1,3})/g, (_, octal) => String.fromCharCode(parseInt(octal, 8)));
}

function cleanExtractedText(text) {
  return text
    .replace(/\s+/g, " ")
    .replace(/\[Page \d+\]\s*/g, (match) => `\n\n${match.trim()} `)
    .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, "")
    .trim();
}
