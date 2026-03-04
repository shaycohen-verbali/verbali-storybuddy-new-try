const state = {
  packages: [],
  editingPackageId: null,
  styleRefs: [],
  characterImageHints: {},
  detectedCharacters: [],
  latestDebugBundle: null,
  runtimeConfig: null,
  askTimer: {
    startedAt: 0,
    intervalId: null,
  },
  speech: {
    recognition: null,
    listening: false,
  },
};

const API_BASE = window.STORYBUDDY_API_BASE || "";
const PACKAGE_CACHE_KEY = "storybuddy.packages.v2";

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
  styleRefList: document.getElementById("styleRefList"),
  clearRefsBtn: document.getElementById("clearRefsBtn"),
  characterMapList: document.getElementById("characterMapList"),
  autoMapCharactersBtn: document.getElementById("autoMapCharactersBtn"),
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
  askElapsed: document.getElementById("askElapsed"),
  timingsView: document.getElementById("timingsView"),
  timelineView: document.getElementById("timelineView"),
  cards: document.getElementById("cards"),
  askCharacterGallery: document.getElementById("askCharacterGallery"),
  debugView: document.getElementById("debugView"),
  copyDebugBtn: document.getElementById("copyDebugBtn"),

  refreshLibraryBtn: document.getElementById("refreshLibraryBtn"),
  newBookBtn: document.getElementById("newBookBtn"),
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
  renderAskCharacterGallery();
  clearRun();
  if (state.runtimeConfig && !state.runtimeConfig.replicateConfigured) {
    const message = "Replicate is not configured. Set REPLICATE_API_TOKEN to generate images.";
    setSetupNote(message);
    el.timingsView.textContent = message;
    return;
  }
  if (state.runtimeConfig && !state.runtimeConfig.answerConfigured) {
    const message = "Gemini is not configured. Set GEMINI_API_KEY to generate answer options.";
    setSetupNote(message);
    el.timingsView.textContent = message;
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
      state.detectedCharacters = extractCharacterHintsFromText(el.bookText.value || "");
      removeBookPageRefs();
      ensureCharacterMappingCoverage();
      renderStyleRefEditor();
      renderCharacterMapEditor();
      setSetupNote("Loaded text file content.");
      return;
    }

    if (isPdf) {
      setSetupNote("PDF selected. Extracting text...");
      try {
        const extraction = await extractPdfTextFromFile(file);
        removeBookPageRefs();
        const extractedRefs = (extraction.styleRefs || []).map((ref, idx) =>
          normalizeStyleRef(ref, `book-${Date.now()}-${idx}`)
        );
        state.styleRefs = dedupeStyleRefs([...state.styleRefs, ...extractedRefs]);
        ensureCharacterMappingCoverage();
        renderStyleRefEditor();
        renderCharacterMapEditor();
        if (extraction.text && extraction.text.length >= 40) {
          el.bookText.value = extraction.text;
          state.detectedCharacters = extractCharacterHintsFromText(el.bookText.value || "");
          if (!el.storyTitle.value.trim()) {
            el.storyTitle.value = file.name.replace(/\.pdf$/i, "");
          }
          setSetupNote(
            `Extracted text from PDF (${extraction.method}, ${extraction.pageCount} page${
              extraction.pageCount === 1 ? "" : "s"
            }). Added ${extractedRefs.length} reference image${extractedRefs.length === 1 ? "" : "s"} from book pages.`
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
    const loaded = await Promise.all(
      files.map(async (file, idx) => ({
        id: `style-${Date.now()}-${idx}`,
        name: file.name,
        dataUrl: await fileToDataUrl(file),
        characterHints: [],
        sceneHints: [],
        sourceType: "manual",
        pageNumber: null,
        pageTextSnippet: "",
      }))
    );
    state.styleRefs = dedupeStyleRefs([...state.styleRefs, ...loaded.map((ref, idx) => normalizeStyleRef(ref, `manual-${Date.now()}-${idx}`))]);
    ensureCharacterMappingCoverage();
    renderStyleRefEditor();
    renderCharacterMapEditor();
    el.styleRefs.value = "";

    setSetupNote(`Loaded ${loaded.length} image${loaded.length === 1 ? "" : "s"}. Total reference images: ${state.styleRefs.length}.`);
  });

  el.clearRefsBtn.addEventListener("click", () => {
    state.styleRefs = [];
    state.characterImageHints = {};
    renderStyleRefEditor();
    renderCharacterMapEditor();
    setSetupNote("Cleared all reference images.");
  });

  el.autoMapCharactersBtn?.addEventListener("click", () => {
    state.characterImageHints = buildAutoCharacterImageHints(el.bookText.value, state.styleRefs, deriveCharacterList());
    ensureCharacterMappingCoverage();
    renderCharacterMapEditor();
    setSetupNote("Auto-mapped characters to reference images.");
  });

  el.bookText.addEventListener("input", () => {
    state.detectedCharacters = extractCharacterHintsFromText(el.bookText.value || "");
    ensureCharacterMappingCoverage();
    renderCharacterMapEditor();
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
        styleRefs: styleRefs.map((ref, idx) => normalizeStyleRef(ref, `save-${idx}`)),
        characterImageHints: buildCharacterImageHints(el.bookText.value, styleRefs),
      };

      const result = await apiRequest("/api/setup/ingest", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      state.editingPackageId = result.package.id;
      state.styleRefs = (result.package.style_refs || []).map((ref, idx) => normalizeStyleRef(ref, `saved-${idx}`));
      state.detectedCharacters = getPackageCharacterProfiles(result.package).map((row) => row.name);
      loadCharacterHintsFromPackage(result.package);
      ensureCharacterMappingCoverage();
      renderStyleRefEditor();
      renderCharacterMapEditor();
      upsertCachedPackage(result.package);
      const mappedCharacters = (result.package.character_style_map || []).filter((m) => (m.ref_ids || []).length).length;
      setSetupNote(
        `Saved ${result.package.title}. Learned ${result.learnedSummary.facts} facts, ${result.learnedSummary.characters} characters, ${mappedCharacters} character-image mappings, ${result.learnedSummary.sceneMappings || 0} scene mappings.`
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

  renderStyleRefEditor();
  renderCharacterMapEditor();
}

function wireAsk() {
  el.packageSelect.addEventListener("change", () => {
    renderAskCharacterGallery();
  });

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
    if (state.runtimeConfig && !state.runtimeConfig.replicateConfigured) {
      el.timingsView.textContent = "Replicate is not configured. Add REPLICATE_API_TOKEN and redeploy.";
      return;
    }
    if (state.runtimeConfig && !state.runtimeConfig.answerConfigured) {
      el.timingsView.textContent = "Gemini is not configured. Add GEMINI_API_KEY and redeploy.";
      return;
    }

    el.generateBtn.disabled = true;
    el.generateBtn.textContent = "Generating...";
    startAskTimer();
    const askStartedAt = performance.now();

    try {
      const result = await apiRequest("/api/ask", {
        method: "POST",
        body: JSON.stringify({ packageId, package: selectedPackage, question, model }),
      });
      state.latestDebugBundle = result.debugBundle;
      await renderAskResult(result);
      stopAskTimer(performance.now() - askStartedAt);
    } catch (err) {
      el.timingsView.textContent = `Ask failed: ${readError(err)}. Check GEMINI_API_KEY and REPLICATE_API_TOKEN in Vercel project settings.`;
      stopAskTimer(performance.now() - askStartedAt);
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
  el.newBookBtn.addEventListener("click", () => {
    resetSetup();
    activateTab("setup");
    setSetupNote("Ready for a new book. Upload a PDF or paste text, then save as a new package.");
  });
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
  let packages = [];
  try {
    const remote = await apiRequest("/api/packages");
    packages = Array.isArray(remote) ? remote : [];
  } catch {
    packages = [];
  }
  if (!packages.length) {
    packages = loadCachedPackages();
  }
  state.packages = Array.isArray(packages) ? packages : [];
  saveCachedPackages(state.packages);
  renderPackageSelect(preferredId);
  renderLibrary();
  renderAskCharacterGallery();
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
  renderAskCharacterGallery();
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
      let deletedRemotely = false;
      try {
        await apiRequest(`/api/packages/${pkg.id}`, { method: "DELETE" });
        deletedRemotely = true;
      } catch (err) {
        setSetupNote(`Delete not found on server. Removing local copy only (${readError(err)}).`);
      }
      removeCachedPackage(pkg.id);
      state.packages = state.packages.filter((entry) => entry.id !== pkg.id);
      if (state.editingPackageId === pkg.id) {
        resetSetup();
      }
      renderPackageSelect();
      renderLibrary();
      if (deletedRemotely) {
        await refreshPackages();
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
  state.styleRefs = (pkg.style_refs || []).map((ref, idx) => normalizeStyleRef(ref, `pkg-${idx}`));
  state.detectedCharacters = getPackageCharacterProfiles(pkg).map((row) => row.name);
  loadCharacterHintsFromPackage(pkg);
  ensureCharacterMappingCoverage();
  el.storyTitle.value = pkg.title || "";
  el.bookText.value = pkg.raw_text || "";
  el.bookFile.value = "";
  renderStyleRefEditor();
  renderCharacterMapEditor();
  setSetupNote(
    `Loaded ${pkg.title}. Character-image mappings: ${(pkg.character_style_map || []).filter((m) => (m.ref_ids || []).length).length}, Scene mappings: ${(pkg.scene_style_map || []).filter((m) => (m.ref_ids || []).length).length}`
  );
}

function clearRun() {
  el.timingsView.textContent = "No run yet.";
  el.timelineView.textContent = "No run yet.";
  el.debugView.textContent = "No run yet.";
  el.cards.innerHTML = "";
  state.latestDebugBundle = null;
  setAskElapsed(0);
}

function waitForImageLoad(img, src) {
  return new Promise((resolve) => {
    const done = () => resolve();
    img.addEventListener("load", done, { once: true });
    img.addEventListener("error", done, { once: true });
    img.src = src;
  });
}

function startAskTimer() {
  stopAskTimer(null);
  state.askTimer.startedAt = performance.now();
  setAskElapsed(0);
  state.askTimer.intervalId = setInterval(() => {
    setAskElapsed(performance.now() - state.askTimer.startedAt);
  }, 100);
}

function stopAskTimer(finalMs) {
  if (state.askTimer.intervalId) {
    clearInterval(state.askTimer.intervalId);
    state.askTimer.intervalId = null;
  }
  if (typeof finalMs === "number") {
    setAskElapsed(finalMs);
  }
}

function setAskElapsed(ms) {
  if (!el.askElapsed) {
    return;
  }
  const seconds = Math.max(0, ms) / 1000;
  el.askElapsed.textContent = `Elapsed: ${seconds.toFixed(2)}s`;
}

async function renderAskResult(result) {
  const stepTimings = result.telemetry?.stepTimings || result.telemetry?.step_timings || {};
  el.timingsView.textContent = JSON.stringify(stepTimings, null, 2);

  const timeline = result.telemetry?.timeline || [];
  el.timelineView.textContent = timeline
    .map((entry) => `${entry.lane.padEnd(7)} | +${String(entry.startMs).padStart(5)}ms | ${String(entry.durationMs).padStart(5)}ms | ${entry.event}`)
    .join("\n");

  el.cards.innerHTML = "";
  const imageLoadPromises = [];
  (result.cards || []).forEach((card) => {
    const node = el.cardTemplate.content.firstElementChild.cloneNode(true);
    const image = node.querySelector(".card__media");
    const text = node.querySelector(".card__text");
    const badge = node.querySelector(".card__badge");

    imageLoadPromises.push(waitForImageLoad(image, card.imageDataUrl));
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

  if (imageLoadPromises.length) {
    await Promise.allSettled(imageLoadPromises);
  }
  el.debugView.textContent = JSON.stringify(result.debugBundle || {}, null, 2);
}

function getPackageCharacterProfiles(pkg) {
  if (!pkg) {
    return [];
  }
  const rawProfiles = Array.isArray(pkg.character_profiles)
    ? pkg.character_profiles
    : Array.isArray(pkg.characterProfiles)
    ? pkg.characterProfiles
    : [];
  const profiles = rawProfiles
    .map((row) => ({
      name: String(row?.name || "").trim(),
      description: String(row?.description || "").trim(),
      species: String(row?.species || "").trim(),
      appearanceTraits: toHintList(row?.appearance_traits || row?.appearanceTraits || []),
      visualVibe: String(row?.visual_vibe || row?.visualVibe || "").trim(),
    }))
    .filter((row) => row.name);
  if (profiles.length) {
    return profiles;
  }
  const names = Array.isArray(pkg.characters) ? pkg.characters : [];
  return names
    .map((name) => ({
      name: String(name || "").trim(),
      description: "",
      species: "",
      appearanceTraits: [],
      visualVibe: "",
    }))
    .filter((row) => row.name);
}

function findCharacterProfile(pkg, name) {
  const target = String(name || "").trim().toLowerCase();
  if (!target) {
    return null;
  }
  return getPackageCharacterProfiles(pkg).find((row) => row.name.toLowerCase() === target) || null;
}

function selectedAskPackage() {
  const packageId = el.packageSelect?.value || "";
  if (!packageId) {
    return null;
  }
  return state.packages.find((pkg) => pkg.id === packageId) || null;
}

function renderAskCharacterGallery() {
  if (!el.askCharacterGallery) {
    return;
  }
  el.askCharacterGallery.innerHTML = "";

  const pkg = selectedAskPackage();
  if (!pkg) {
    const empty = document.createElement("p");
    empty.className = "inline-note";
    empty.textContent = "Select a package to view character-image mapping.";
    el.askCharacterGallery.appendChild(empty);
    return;
  }

  const characterProfiles = getPackageCharacterProfiles(pkg);
  const refs = Array.isArray(pkg.style_refs) ? pkg.style_refs.map((ref, idx) => normalizeStyleRef(ref, `ask-ref-${idx}`)) : [];
  const refLookup = new Map(refs.map((ref) => [ref.id, ref]));
  const mapRows = Array.isArray(pkg.character_style_map) ? pkg.character_style_map : [];
  const mapLookup = new Map(mapRows.map((row) => [String(row.character || ""), row]));

  if (!characterProfiles.length) {
    const empty = document.createElement("p");
    empty.className = "inline-note";
    empty.textContent = "No detected characters in this package yet. Open Setup and save the package again.";
    el.askCharacterGallery.appendChild(empty);
    return;
  }

  characterProfiles.forEach((characterProfile) => {
    const character = characterProfile.name;
    const mapRow = mapLookup.get(character) || null;
    const refIds = toHintList(mapRow?.ref_ids || []);
    const mappedRef = refLookup.get(refIds[0] || "") || null;
    const description = mapRow?.description || characterProfile.description || "";
    const species = mapRow?.species || characterProfile.species || "";
    const visualVibe = mapRow?.visual_vibe || mapRow?.visualVibe || characterProfile.visualVibe || "";
    const appearanceTraits = toHintList(
      mapRow?.appearance_traits || mapRow?.appearanceTraits || characterProfile.appearanceTraits || []
    );

    const card = document.createElement("article");
    card.className = "characterGalleryItem";

    const img = document.createElement("img");
    img.className = "characterGalleryItem__image";
    img.alt = mappedRef ? `${character} mapped image` : `${character} missing image`;
    img.src =
      mappedRef?.dataUrl ||
      "data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='130'%3E%3Crect width='100%25' height='100%25' fill='%23f3efe2'/%3E%3Ctext x='50%25' y='50%25' dominant-baseline='middle' text-anchor='middle' fill='%237a7567' font-size='13' font-family='Arial'%3ENo mapped image%3C/text%3E%3C/svg%3E";

    const name = document.createElement("p");
    name.className = "characterGalleryItem__name";
    name.textContent = character;

    const meta = document.createElement("p");
    meta.className = "characterGalleryItem__meta";
    meta.textContent = description || "No description yet";
    const speciesMeta = document.createElement("p");
    speciesMeta.className = "characterGalleryItem__meta";
    speciesMeta.textContent = species ? `Species: ${species}` : "Species: unknown";
    const vibeMeta = document.createElement("p");
    vibeMeta.className = "characterGalleryItem__meta";
    vibeMeta.textContent = visualVibe ? `Visual vibe: ${visualVibe}` : "";

    const mapping = document.createElement("p");
    mapping.className = "characterGalleryItem__meta";
    mapping.textContent = mappedRef ? `Image: ${mappedRef.name}` : "Image: no mapping";

    const traits = document.createElement("p");
    traits.className = "characterGalleryItem__meta";
    traits.textContent = appearanceTraits.length
      ? `Traits: ${appearanceTraits.slice(0, 4).join(", ")}`
      : "Traits: not available";

    card.appendChild(img);
    card.appendChild(name);
    card.appendChild(meta);
    card.appendChild(speciesMeta);
    if (vibeMeta.textContent) {
      card.appendChild(vibeMeta);
    }
    card.appendChild(traits);
    card.appendChild(mapping);
    el.askCharacterGallery.appendChild(card);
  });
}

function currentEditingPackage() {
  return state.packages.find((pkg) => pkg.id === state.editingPackageId);
}

function resetSetup() {
  state.editingPackageId = null;
  state.styleRefs = [];
  state.characterImageHints = {};
  state.detectedCharacters = [];
  el.storyTitle.value = "";
  el.bookText.value = "";
  el.bookFile.value = "";
  el.styleRefs.value = "";
  renderStyleRefEditor();
  renderCharacterMapEditor();
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
  const characters = deriveCharacterList();
  const auto = buildAutoCharacterImageHints(text, styleRefs, characters);
  const hintMap = {};

  characters.forEach((character) => {
    const manual = toHintList(state.characterImageHints[character] || []).filter((id) =>
      styleRefs.some((ref) => ref.id === id)
    );
    const chosen = manual.length ? manual : toHintList(auto[character] || []);
    if (chosen.length) {
      hintMap[character] = chosen.slice(0, 2);
    }
  });

  return hintMap;
}

function deriveCharacterList() {
  const pkg = currentEditingPackage();
  const packageCharacters = getPackageCharacterProfiles(pkg).map((row) => row.name);
  const explicitDetected = Array.isArray(state.detectedCharacters) ? state.detectedCharacters : [];
  const hintKeys = Object.keys(state.characterImageHints || {});
  const textCharacters = extractCharacterHintsFromText(el.bookText.value || "");
  const preferred = explicitDetected.length ? explicitDetected : packageCharacters;
  const merged = preferred.length ? [...preferred, ...hintKeys] : [...hintKeys, ...textCharacters];
  return dedupeStrings(merged.map((name) => String(name || "").trim()).filter(Boolean)).slice(0, 30);
}

function buildAutoCharacterImageHints(text, styleRefs, characters) {
  const hints = {};
  const selectedCharacters = Array.isArray(characters) && characters.length ? characters : extractCharacterHintsFromText(text);

  styleRefs.forEach((ref) => {
    toHintList(ref.characterHints).forEach((character) => {
      if (!selectedCharacters.includes(character)) {
        return;
      }
      if (!hints[character]) {
        hints[character] = [];
      }
      if (!hints[character].includes(ref.id)) {
        hints[character].push(ref.id);
      }
    });
  });

  selectedCharacters.forEach((character) => {
    const tokens = character.toLowerCase().split(/\s+/).filter(Boolean);
    const ids = styleRefs
      .filter((ref) => {
        const blob = `${ref.name} ${(ref.pageTextSnippet || "").slice(0, 220)} ${(ref.characterHints || []).join(" ")}`.toLowerCase();
        return tokens.some((tok) => tok.length >= 3 && blob.includes(tok));
      })
      .map((ref) => ref.id)
      .slice(0, 2);
    if (ids.length) {
      hints[character] = dedupeStrings([...(hints[character] || []), ...ids]).slice(0, 2);
    }
  });

  const firstRefId = styleRefs[0]?.id;
  if (firstRefId) {
    selectedCharacters.forEach((character) => {
      if (!(hints[character] || []).length) {
        hints[character] = [firstRefId];
      }
    });
  }

  return hints;
}

function loadCharacterHintsFromPackage(pkg) {
  const next = {};
  (pkg?.character_style_map || []).forEach((row) => {
    const character = String(row.character || "").trim();
    if (!character) {
      return;
    }
    const ids = toHintList(row.ref_ids || []).filter((id) => state.styleRefs.some((ref) => ref.id === id));
    if (ids.length) {
      next[character] = ids.slice(0, 2);
    }
  });
  state.characterImageHints = next;
}

function ensureCharacterMappingCoverage() {
  const characters = deriveCharacterList();
  const auto = buildAutoCharacterImageHints(el.bookText.value, state.styleRefs, characters);
  const validRefIds = new Set(state.styleRefs.map((ref) => ref.id));
  const next = {};

  characters.forEach((character) => {
    const existing = toHintList(state.characterImageHints[character] || []).filter((id) => validRefIds.has(id));
    const chosen = existing.length ? existing : toHintList(auto[character] || []);
    if (chosen.length) {
      next[character] = chosen.slice(0, 2);
    }
  });

  state.characterImageHints = next;
}

function normalizeStyleRef(raw, fallbackId) {
  const id = raw.id || fallbackId;
  return {
    id,
    name: String(raw.name || id),
    dataUrl: raw.dataUrl || raw.data_url || "",
    characterHints: toHintList(raw.characterHints || raw.character_hints),
    sceneHints: toHintList(raw.sceneHints || raw.scene_hints),
    sourceType: String(raw.sourceType || raw.source_type || "manual"),
    pageNumber: raw.pageNumber ?? raw.page_number ?? null,
    pageTextSnippet: String(raw.pageTextSnippet || raw.page_text_snippet || ""),
  };
}

function dedupeStyleRefs(refs) {
  const seen = new Set();
  const out = [];
  refs.forEach((entry, idx) => {
    const ref = normalizeStyleRef(entry, `style-${Date.now()}-${idx}`);
    if (!ref.dataUrl) {
      return;
    }
    if (seen.has(ref.id)) {
      return;
    }
    seen.add(ref.id);
    out.push(ref);
  });
  return out;
}

function toHintList(value) {
  if (Array.isArray(value)) {
    return dedupeStrings(value.map((v) => String(v).trim()).filter(Boolean));
  }
  if (typeof value === "string") {
    return dedupeStrings(
      value
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean)
    );
  }
  return [];
}

function dedupeStrings(values) {
  return Array.from(new Set(values));
}

function removeBookPageRefs() {
  state.styleRefs = state.styleRefs.filter((ref) => ref.sourceType !== "book_page");
}

function renderStyleRefEditor() {
  if (!el.styleRefList) {
    return;
  }
  el.styleRefList.innerHTML = "";
  if (!state.styleRefs.length) {
    const empty = document.createElement("p");
    empty.className = "inline-note";
    empty.textContent = "No reference images yet. Upload images or a PDF to auto-extract page references.";
    el.styleRefList.appendChild(empty);
    return;
  }

  state.styleRefs.forEach((ref) => {
    const card = document.createElement("article");
    card.className = "styleRefItem";

    const preview = document.createElement("img");
    preview.className = "styleRefItem__preview";
    preview.src = ref.dataUrl;
    preview.alt = ref.name;

    const fields = document.createElement("div");
    fields.className = "styleRefItem__fields";

    const sourceMeta = document.createElement("p");
    sourceMeta.className = "styleRefItem__meta";
    sourceMeta.textContent = `Source: ${ref.sourceType}${ref.pageNumber ? ` | Page ${ref.pageNumber}` : ""}`;
    fields.appendChild(sourceMeta);

    const nameInput = document.createElement("input");
    nameInput.value = ref.name;
    nameInput.placeholder = "Reference name";
    nameInput.addEventListener("input", () => {
      ref.name = nameInput.value.trim() || ref.id;
      ensureCharacterMappingCoverage();
      renderCharacterMapEditor();
    });
    fields.appendChild(wrapRefField("Name", nameInput));

    const charsInput = document.createElement("input");
    charsInput.value = ref.characterHints.join(", ");
    charsInput.placeholder = "Character hints (comma separated)";
    charsInput.addEventListener("input", () => {
      ref.characterHints = toHintList(charsInput.value);
      ensureCharacterMappingCoverage();
      renderCharacterMapEditor();
    });
    fields.appendChild(wrapRefField("Character hints", charsInput));

    const scenesInput = document.createElement("input");
    scenesInput.value = ref.sceneHints.join(", ");
    scenesInput.placeholder = "Scene hints (comma separated)";
    scenesInput.addEventListener("input", () => {
      ref.sceneHints = toHintList(scenesInput.value);
    });
    fields.appendChild(wrapRefField("Scene hints", scenesInput));

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "btn btn--ghost";
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", () => {
      state.styleRefs = state.styleRefs.filter((entry) => entry.id !== ref.id);
      ensureCharacterMappingCoverage();
      renderStyleRefEditor();
      renderCharacterMapEditor();
    });
    fields.appendChild(removeBtn);

    card.appendChild(preview);
    card.appendChild(fields);
    el.styleRefList.appendChild(card);
  });
}

function renderCharacterMapEditor() {
  if (!el.characterMapList) {
    return;
  }

  el.characterMapList.innerHTML = "";
  const characters = deriveCharacterList();
  if (!characters.length) {
    const empty = document.createElement("p");
    empty.className = "inline-note";
    empty.textContent = "No characters detected yet. Add book text or save the package first.";
    el.characterMapList.appendChild(empty);
    return;
  }

  if (!state.styleRefs.length) {
    const empty = document.createElement("p");
    empty.className = "inline-note";
    empty.textContent = "No reference images available yet. Upload PDF/image refs to map character images.";
    el.characterMapList.appendChild(empty);
    return;
  }

  ensureCharacterMappingCoverage();

  const pkg = currentEditingPackage();
  characters.forEach((character) => {
    const mappedIds = toHintList(state.characterImageHints[character] || []);
    const selectedRefId = mappedIds[0] || "";
    const selectedRef = state.styleRefs.find((ref) => ref.id === selectedRefId) || null;
    const profile = findCharacterProfile(pkg, character);
    const description = profile?.description || "";
    const species = profile?.species || "";
    const vibe = profile?.visualVibe || "";
    const traits = toHintList(profile?.appearanceTraits || []);

    const card = document.createElement("article");
    card.className = "characterMapItem";

    const previewWrap = document.createElement("div");
    previewWrap.className = "characterMapItem__previewWrap";
    const preview = document.createElement("img");
    preview.className = "characterMapItem__preview";
    preview.alt = selectedRef ? `${character} mapped reference` : `${character} no mapped reference`;
    preview.src =
      selectedRef?.dataUrl ||
      "data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='180'%3E%3Crect width='100%25' height='100%25' fill='%23f3efe2'/%3E%3Ctext x='50%25' y='50%25' dominant-baseline='middle' text-anchor='middle' fill='%237a7567' font-size='14' font-family='Arial'%3ENo image%3C/text%3E%3C/svg%3E";
    previewWrap.appendChild(preview);

    const fields = document.createElement("div");
    fields.className = "characterMapItem__fields";

    const title = document.createElement("h4");
    title.className = "characterMapItem__title";
    title.textContent = character;
    fields.appendChild(title);

    const desc = document.createElement("p");
    desc.className = "characterMapItem__meta";
    desc.textContent = description || "No character description yet. Save setup to generate AI descriptions.";
    fields.appendChild(desc);

    const speciesRow = document.createElement("p");
    speciesRow.className = "characterMapItem__meta";
    speciesRow.textContent = species ? `Species: ${species}` : "Species: unknown";
    fields.appendChild(speciesRow);

    if (vibe) {
      const vibeRow = document.createElement("p");
      vibeRow.className = "characterMapItem__meta";
      vibeRow.textContent = `Visual vibe: ${vibe}`;
      fields.appendChild(vibeRow);
    }

    if (traits.length) {
      const traitsRow = document.createElement("p");
      traitsRow.className = "characterMapItem__meta";
      traitsRow.textContent = `Traits: ${traits.slice(0, 4).join(", ")}`;
      fields.appendChild(traitsRow);
    }

    const status = document.createElement("p");
    status.className = "characterMapItem__meta";
    status.textContent = selectedRef ? `Mapped to: ${selectedRef.name}` : "Not mapped";
    fields.appendChild(status);

    const select = document.createElement("select");
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "Select image reference";
    select.appendChild(blank);

    state.styleRefs.forEach((ref) => {
      const option = document.createElement("option");
      option.value = ref.id;
      option.textContent = `${ref.name}${ref.pageNumber ? ` (page ${ref.pageNumber})` : ""}`;
      if (ref.id === selectedRefId) {
        option.selected = true;
      }
      select.appendChild(option);
    });

    select.addEventListener("change", () => {
      const value = select.value.trim();
      if (!value) {
        delete state.characterImageHints[character];
      } else {
        state.characterImageHints[character] = [value];
      }
      ensureCharacterMappingCoverage();
      renderCharacterMapEditor();
    });

    fields.appendChild(wrapRefField("Reference image", select));
    card.appendChild(previewWrap);
    card.appendChild(fields);
    el.characterMapList.appendChild(card);
  });
}

function wrapRefField(label, inputEl) {
  const wrapper = document.createElement("label");
  wrapper.className = "field";
  const title = document.createElement("span");
  title.textContent = label;
  wrapper.appendChild(title);
  wrapper.appendChild(inputEl);
  return wrapper;
}

function extractCharacterHintsFromText(text) {
  const matches = (text || "").match(/\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b/g) || [];
  const banned = new Set([
    "The", "A", "An", "And", "But", "When", "Then", "After", "Before", "In", "On", "At", "Inside", "Outside",
    "Page", "Back", "Every", "Tuesday", "Thursday", "By", "With", "Copyright", "Inc", "Story", "Book",
  ]);
  return dedupeStrings(matches.map((name) => name.trim())).filter((name) => {
    if (!name || name.length < 3) {
      return false;
    }
    if (banned.has(name)) {
      return false;
    }
    if (/^[A-Z]{2,}$/.test(name)) {
      return false;
    }
    return true;
  });
}

function extractSceneHintsFromText(text) {
  const source = String(text || "");
  const scenes = [];
  const regex = /\b(in|at|on|near|inside|outside|by)\b([^.!?,;\n]+)/gi;
  let match;
  while ((match = regex.exec(source))) {
    scenes.push(`${match[1]} ${match[2].trim()}`.replace(/\s+/g, " "));
    if (scenes.length >= 3) {
      break;
    }
  }
  return dedupeStrings(scenes);
}

function loadCachedPackages() {
  try {
    const raw = localStorage.getItem(PACKAGE_CACHE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveCachedPackages(packages) {
  try {
    localStorage.setItem(PACKAGE_CACHE_KEY, JSON.stringify(Array.isArray(packages) ? packages : []));
  } catch {
    // Ignore localStorage write failures.
  }
}

function upsertCachedPackage(pkg) {
  const current = loadCachedPackages();
  const filtered = current.filter((entry) => entry.id !== pkg.id);
  const next = [pkg, ...filtered];
  saveCachedPackages(next);
}

function removeCachedPackage(packageId) {
  const next = loadCachedPackages().filter((entry) => entry.id !== packageId);
  saveCachedPackages(next);
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
    const styleRefs = [];
    const maxRefPages = Math.min(doc.numPages, 8);
    for (let pageNumber = 1; pageNumber <= doc.numPages; pageNumber += 1) {
      const page = await doc.getPage(pageNumber);
      const textContent = await page.getTextContent();
      const pageText = textContent.items
        .map((item) => (typeof item.str === "string" ? item.str : ""))
        .join(" ");
      pages.push(`[Page ${pageNumber}] ${pageText}`);

      if (pageNumber <= maxRefPages) {
        const viewport = page.getViewport({ scale: 1 });
        const desiredWidth = 420;
        const scale = Math.max(0.4, Math.min(1.1, desiredWidth / Math.max(1, viewport.width)));
        const renderViewport = page.getViewport({ scale });
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.floor(renderViewport.width));
        canvas.height = Math.max(1, Math.floor(renderViewport.height));
        const context = canvas.getContext("2d");
        if (context) {
          await page.render({ canvasContext: context, viewport: renderViewport }).promise;
          const artDataUrl = extractIllustrationDataUrl(canvas, textContent.items || []);
          styleRefs.push({
            id: `book-page-${Date.now()}-${pageNumber}`,
            name: `${file.name.replace(/\.pdf$/i, "")} page ${pageNumber}`,
            dataUrl: artDataUrl,
            characterHints: extractCharacterHintsFromText(pageText).slice(0, 4),
            sceneHints: extractSceneHintsFromText(pageText).slice(0, 3),
            sourceType: "book_page",
            pageNumber,
            pageTextSnippet: pageText.slice(0, 180),
          });
        }
      }
    }

    return {
      text: cleanExtractedText(pages.join("\n\n")),
      pageCount: doc.numPages,
      method: "pdf.js",
      styleRefs,
    };
  } catch {
    return {
      text: extractPdfTextHeuristic(arrayBuffer),
      pageCount: 1,
      method: "heuristic",
      styleRefs: [],
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

function extractIllustrationDataUrl(canvas, textItems) {
  const width = canvas.width;
  const height = canvas.height;
  if (!width || !height) {
    return canvas.toDataURL("image/jpeg", 0.82);
  }

  const colDensity = Array(width).fill(0);
  const rowDensity = Array(height).fill(0);

  (textItems || []).forEach((item) => {
    const str = typeof item.str === "string" ? item.str.trim() : "";
    if (!str) {
      return;
    }
    const tr = Array.isArray(item.transform) ? item.transform : [];
    const x = Number(tr[4] || 0);
    const y = Number(tr[5] || 0);
    const w = Math.max(1, Math.ceil(Number(item.width || 0)));
    const h = Math.max(8, Math.ceil(Math.abs(Number(tr[3] || item.height || 10))));

    const x0 = clampInt(Math.floor(x), 0, width - 1);
    const x1 = clampInt(Math.ceil(x + w), x0 + 1, width);
    const yTop = clampInt(Math.floor(height - y - h), 0, height - 1);
    const yBottom = clampInt(Math.ceil(height - y + h * 0.2), yTop + 1, height);

    for (let xi = x0; xi < x1; xi += 1) {
      colDensity[xi] += 1;
    }
    for (let yi = yTop; yi < yBottom; yi += 1) {
      rowDensity[yi] += 1;
    }
  });

  let cropX = 0;
  let cropW = width;
  const horizontalCrop = findSparseRegionBySplit(colDensity, 0.45, 1.7);
  if (horizontalCrop) {
    cropX = horizontalCrop.start;
    cropW = horizontalCrop.end - horizontalCrop.start;
  }

  let cropY = 0;
  let cropH = height;
  const verticalCrop = findSparseRegionBySplit(rowDensity, 0.55, 1.4);
  if (verticalCrop) {
    cropY = verticalCrop.start;
    cropH = verticalCrop.end - verticalCrop.start;
  }

  if (cropW * cropH < width * height * 0.45) {
    return canvas.toDataURL("image/jpeg", 0.82);
  }

  const out = document.createElement("canvas");
  out.width = cropW;
  out.height = cropH;
  const ctx = out.getContext("2d");
  if (!ctx) {
    return canvas.toDataURL("image/jpeg", 0.82);
  }
  ctx.drawImage(canvas, cropX, cropY, cropW, cropH, 0, 0, cropW, cropH);
  return out.toDataURL("image/jpeg", 0.82);
}

function findSparseRegionBySplit(density, minFraction, ratioThreshold) {
  const len = density.length;
  const minSide = Math.max(1, Math.floor(len * minFraction));
  const start = Math.floor(len * 0.2);
  const end = Math.ceil(len * 0.8);
  const step = Math.max(4, Math.floor(len / 50));
  const prefix = [0];
  for (let i = 0; i < len; i += 1) {
    prefix.push(prefix[prefix.length - 1] + density[i]);
  }

  let best = null;
  for (let split = start; split <= end; split += step) {
    const leftLen = split;
    const rightLen = len - split;
    if (leftLen < minSide && rightLen < minSide) {
      continue;
    }
    const leftAvg = leftLen > 0 ? (prefix[split] - prefix[0]) / leftLen : Number.POSITIVE_INFINITY;
    const rightAvg = rightLen > 0 ? (prefix[len] - prefix[split]) / rightLen : Number.POSITIVE_INFINITY;

    if (rightLen >= minSide && leftAvg > rightAvg * ratioThreshold) {
      if (!best || rightAvg < best.score) {
        best = { start: split, end: len, score: rightAvg };
      }
    }
    if (leftLen >= minSide && rightAvg > leftAvg * ratioThreshold) {
      if (!best || leftAvg < best.score) {
        best = { start: 0, end: split, score: leftAvg };
      }
    }
  }

  return best ? { start: best.start, end: best.end } : null;
}

function clampInt(value, min, max) {
  return Math.max(min, Math.min(max, value));
}
