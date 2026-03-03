const STORAGE_KEY = "storybuddy_packages_v1";

const state = {
  packages: [],
  editingPackageId: null,
  setup: {
    learned: null,
    uploadedStyleRefs: [],
  },
  run: {
    latestDebugBundle: null,
  },
  speech: {
    recognition: null,
    listening: false,
  },
};

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

init();

function init() {
  loadPackages();
  wireTabs();
  wireSetup();
  wireAsk();
  wireLibrary();
  renderPackageSelect();
  renderLibrary();
  setupSpeech();
}

function wireTabs() {
  el.tabs.forEach((tabButton) => {
    tabButton.addEventListener("click", () => {
      const tabName = tabButton.dataset.tab;
      el.tabs.forEach((button) => {
        button.classList.toggle("is-active", button === tabButton);
        button.setAttribute("aria-selected", button === tabButton ? "true" : "false");
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

    if (/text|json|markdown/.test(file.type) || /\.(txt|md|json)$/i.test(file.name)) {
      const content = await file.text();
      el.bookText.value = content;
      setSetupNote("Loaded text content from file.");
      return;
    }

    if (/\.pdf$/i.test(file.name) || file.type === "application/pdf") {
      setSetupNote("PDF uploaded. Extracting text...");
      try {
        const extraction = await extractTextFromPdf(file);
        if (!extraction.text || extraction.text.length < 40) {
          setSetupNote(
            "PDF uploaded, but extraction found too little text. Paste story text manually for best results."
          );
          return;
        }

        el.bookText.value = extraction.text;
        setSetupNote(
          `Extracted text from PDF (${extraction.method}, ${extraction.pageCount} page${
            extraction.pageCount === 1 ? "" : "s"
          }).`
        );
      } catch (error) {
        setSetupNote(
          `PDF extraction failed: ${error instanceof Error ? error.message : String(
            error
          )}. Paste story text manually.`
        );
      }
      return;
    }

    setSetupNote("Unsupported file type for extraction. Please paste story text manually.");
  });

  el.styleRefs.addEventListener("change", async () => {
    const files = Array.from(el.styleRefs.files || []);
    if (!files.length) {
      state.setup.uploadedStyleRefs = [];
      return;
    }

    const refs = await Promise.all(
      files.map(async (file, idx) => ({
        id: `style-${Date.now()}-${idx}`,
        name: file.name,
        dataUrl: await fileToDataUrl(file),
      }))
    );

    state.setup.uploadedStyleRefs = refs;
    setSetupNote(`Loaded ${refs.length} style reference image${refs.length === 1 ? "" : "s"}.`);
  });

  el.learnBtn.addEventListener("click", () => {
    const text = el.bookText.value.trim();
    if (!text) {
      setSetupNote("Add story text before learning facts.");
      return;
    }

    const learned = analyzeBookText(text);
    state.setup.learned = learned;
    setSetupNote(
      `Learned ${learned.facts.length} facts, ${learned.characters.length} characters, ${learned.objects.length} objects, ${learned.scenes.length} scenes.`
    );
  });

  el.savePackageBtn.addEventListener("click", () => {
    const title = el.storyTitle.value.trim();
    const text = el.bookText.value.trim();

    if (!title) {
      setSetupNote("Please enter a story title.");
      return;
    }

    if (!text) {
      setSetupNote("Please add story text.");
      return;
    }

    if (!state.setup.learned) {
      state.setup.learned = analyzeBookText(text);
    }

    const existing = state.packages.find((pkg) => pkg.id === state.editingPackageId);
    const fallbackRefs = existing?.styleRefs || [];
    const refs = state.setup.uploadedStyleRefs.length ? state.setup.uploadedStyleRefs : fallbackRefs;
    const nowIso = new Date().toISOString();

    const pkg = {
      id: state.editingPackageId || `pkg-${Date.now()}`,
      title,
      rawText: text,
      createdAt: existing?.createdAt || nowIso,
      updatedAt: nowIso,
      facts: state.setup.learned.facts,
      scenes: state.setup.learned.scenes,
      characters: state.setup.learned.characters,
      objects: state.setup.learned.objects,
      styleNotes: state.setup.learned.styleNotes,
      styleRefs: refs,
    };

    if (existing) {
      state.packages = state.packages.map((entry) => (entry.id === pkg.id ? pkg : entry));
      setSetupNote(`Updated story package: ${title}`);
    } else {
      state.packages.unshift(pkg);
      setSetupNote(`Saved new story package: ${title}`);
    }

    persistPackages();
    renderPackageSelect(pkg.id);
    renderLibrary();
  });

  el.resetSetupBtn.addEventListener("click", () => {
    resetSetupForm();
    setSetupNote("Setup form reset.");
  });
}

function wireAsk() {
  el.generateBtn.addEventListener("click", async () => {
    const pkgId = el.packageSelect.value;
    const question = el.questionInput.value.trim();
    const model = el.modelSelect.value;
    const pkg = state.packages.find((entry) => entry.id === pkgId);

    if (!pkg) {
      setRunError("Select a story package first.");
      return;
    }

    if (!question) {
      setRunError("Enter or dictate a question first.");
      return;
    }

    el.generateBtn.disabled = true;
    el.generateBtn.textContent = "Generating...";

    try {
      const result = await runPipeline({ pkg, question, model });
      renderRunResult(result);
    } catch (error) {
      setRunError(`Run failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      el.generateBtn.disabled = false;
      el.generateBtn.textContent = "Generate 3 Answer Cards";
    }
  });

  el.clearRunBtn.addEventListener("click", () => {
    clearRunSurface();
  });

  el.copyDebugBtn.addEventListener("click", async () => {
    if (!state.run.latestDebugBundle) {
      return;
    }
    try {
      await navigator.clipboard.writeText(JSON.stringify(state.run.latestDebugBundle, null, 2));
      el.copyDebugBtn.textContent = "Copied";
      setTimeout(() => {
        el.copyDebugBtn.textContent = "Copy JSON";
      }, 1000);
    } catch {
      el.copyDebugBtn.textContent = "Copy failed";
      setTimeout(() => {
        el.copyDebugBtn.textContent = "Copy JSON";
      }, 1000);
    }
  });
}

function wireLibrary() {
  el.refreshLibraryBtn.addEventListener("click", renderLibrary);
}

function setupSpeech() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    el.speechStatus.textContent = "Speech API unavailable in this browser";
    el.speechBtn.disabled = true;
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
    const transcript = event.results?.[0]?.[0]?.transcript || "";
    if (transcript) {
      el.questionInput.value = transcript.trim();
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

async function runPipeline({ pkg, question, model }) {
  const t0 = performance.now();
  const events = [];
  const stepTimings = {};

  const startEvent = (name, lane, meta = {}) => {
    const event = {
      name,
      lane,
      meta,
      startMs: performance.now() - t0,
      endMs: null,
      durationMs: null,
    };
    events.push(event);
    return event;
  };

  const endEvent = (event) => {
    event.endMs = performance.now() - t0;
    event.durationMs = event.endMs - event.startMs;
  };

  const transcribeEvent = startEvent("transcription", "main");
  await wait(150);
  const transcript = question;
  endEvent(transcribeEvent);
  stepTimings.transcriptionMs = roundMs(transcribeEvent.durationMs);

  const optionsEvent = startEvent("answer_option_generation", "main");
  const options = generateAnswerOptions(pkg, transcript);
  await wait(100);
  endEvent(optionsEvent);
  stepTimings.answerOptionGenerationMs = roundMs(optionsEvent.durationMs);

  const fanoutEvent = startEvent("image_fanout", "main", { cardCount: 3 });
  const cardResults = await Promise.all(
    options.map((option, idx) =>
      generateCard({
        pkg,
        model,
        question: transcript,
        option,
        cardIndex: idx,
        t0,
        events,
      })
    )
  );
  endEvent(fanoutEvent);
  stepTimings.imageFanoutMs = roundMs(fanoutEvent.durationMs);

  const interactiveEvent = startEvent("last_image_interactive", "main");
  await wait(20);
  endEvent(interactiveEvent);

  const totalMs = roundMs(performance.now() - t0);
  stepTimings.totalMs = totalMs;

  const timeline = events
    .sort((a, b) => a.startMs - b.startMs)
    .map((event) => ({
      lane: event.lane,
      event: event.name,
      startMs: roundMs(event.startMs),
      durationMs: roundMs(event.durationMs || 0),
      endMs: roundMs(event.endMs || event.startMs),
      meta: event.meta,
    }));

  const cards = cardResults.map((result) => ({
    text: result.option.text,
    isCorrect: result.option.isCorrect,
    imageDataUrl: result.imageDataUrl,
    cardTiming: result.cardTiming,
    debug: result.debug,
  }));

  const debugBundle = {
    request: {
      storyPackageId: pkg.id,
      storyTitle: pkg.title,
      model,
      transcript,
    },
    options: options.map((option) => ({
      text: option.text,
      isCorrect: option.isCorrect,
      supportFact: option.supportFact,
    })),
    cards: cards.map((card, idx) => ({
      id: `card-${idx + 1}`,
      ...card.debug,
      cardTiming: card.cardTiming,
    })),
    telemetry: {
      stepTimings,
      timeline,
      completedAt: new Date().toISOString(),
    },
  };

  state.run.latestDebugBundle = debugBundle;

  return {
    cards,
    telemetry: {
      stepTimings,
      timeline,
    },
    debugBundle,
  };
}

async function generateCard({ pkg, model, question, option, cardIndex, t0, events }) {
  const lane = `card-${cardIndex + 1}`;
  const step = {};

  const startEvent = (name, meta = {}) => {
    const event = {
      name,
      lane,
      meta,
      startMs: performance.now() - t0,
      endMs: null,
      durationMs: null,
    };
    events.push(event);
    return event;
  };

  const endEvent = (event) => {
    event.endMs = performance.now() - t0;
    event.durationMs = event.endMs - event.startMs;
  };

  const participantsEvent = startEvent("participant_resolver");
  const participants = resolveParticipants(pkg, option.text, option.supportFact);
  await wait(80 + randomInt(30));
  endEvent(participantsEvent);
  step.participantResolverMs = roundMs(participantsEvent.durationMs);

  const styleEvent = startEvent("style_ref_selection");
  const styleRefsUsed = selectStyleRefs(pkg, participants);
  await wait(50 + randomInt(20));
  endEvent(styleEvent);
  step.styleRefSelectionMs = roundMs(styleEvent.durationMs);

  const planEvent = startEvent("illustration_plan");
  const illustrationPlan = createIllustrationPlan({ pkg, question, option, participants, styleRefsUsed });
  await wait(70 + randomInt(30));
  endEvent(planEvent);
  step.illustrationPlanMs = roundMs(planEvent.durationMs);

  const imageEvent = startEvent("image_generation", { model });
  const imageDelayByModel = {
    "nano-banana-2": 850,
    pro: 1300,
    standard: 1050,
  };

  await wait((imageDelayByModel[model] || 1000) + randomInt(350));
  const imageDataUrl = renderCardIllustration({ pkg, option, participants, cardIndex, model });
  endEvent(imageEvent);
  step.imageGenerationMs = roundMs(imageEvent.durationMs);

  return {
    option,
    imageDataUrl,
    cardTiming: {
      ...step,
      totalMs: roundMs(Object.values(step).reduce((sum, value) => sum + value, 0)),
    },
    debug: {
      prompts: {
        optionPrompt: `Generate a child-friendly answer option for: ${question}`,
        illustrationPrompt: illustrationPlan,
      },
      selectedParticipants: participants,
      styleRefsUsed,
      modelUsed: model,
      generationError: null,
      supportFact: option.supportFact,
    },
  };
}

function generateAnswerOptions(pkg, question) {
  const facts = pkg.facts.slice(0, 18);
  const scoredFacts = facts
    .map((fact) => ({ fact, score: scoreFactAgainstQuestion(fact, question) }))
    .sort((a, b) => b.score - a.score);

  const correctFact = scoredFacts[0]?.fact || facts[0] || pkg.rawText.split(/[.!?]/)[0] || "";
  const wrongFacts = scoredFacts.filter((entry) => entry.fact !== correctFact).map((entry) => entry.fact);

  const correctAnswer = answerFromFact(question, correctFact, pkg);

  const distractorAnswers = [];
  for (const fact of wrongFacts) {
    const candidate = answerFromFact(question, fact, pkg);
    if (candidate && normalize(candidate) !== normalize(correctAnswer) && !distractorAnswers.includes(candidate)) {
      distractorAnswers.push(candidate);
    }
    if (distractorAnswers.length === 2) {
      break;
    }
  }

  while (distractorAnswers.length < 2) {
    distractorAnswers.push(buildSyntheticDistractor(pkg, correctAnswer, distractorAnswers));
  }

  const options = [
    { text: correctAnswer, isCorrect: true, supportFact: correctFact },
    { text: distractorAnswers[0], isCorrect: false, supportFact: wrongFacts[0] || "Synthetic distractor" },
    { text: distractorAnswers[1], isCorrect: false, supportFact: wrongFacts[1] || "Synthetic distractor" },
  ];

  return shuffle(options).slice(0, 3);
}

function resolveParticipants(pkg, optionText, supportFact) {
  const text = `${optionText} ${supportFact}`.toLowerCase();
  const characters = pkg.characters.filter((name) => text.includes(name.toLowerCase()));
  const objects = pkg.objects.filter((item) => text.includes(item.toLowerCase()));
  const sceneHint = pkg.scenes.find((scene) => text.includes(scene.toLowerCase()));

  return {
    scene: sceneHint || pkg.scenes[0] || "story setting",
    characters: characters.slice(0, 3),
    objects: objects.slice(0, 3),
  };
}

function selectStyleRefs(pkg, participants) {
  if (!pkg.styleRefs.length) {
    return [];
  }

  const names = participants.characters.concat(participants.objects).map((item) => item.toLowerCase());
  const ranked = pkg.styleRefs
    .map((ref) => {
      const score = names.some((name) => ref.name.toLowerCase().includes(name)) ? 2 : 1;
      return { ref, score };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, 2)
    .map((entry) => ({ id: entry.ref.id, name: entry.ref.name }));

  return ranked;
}

function createIllustrationPlan({ pkg, question, option, participants, styleRefsUsed }) {
  const refsText = styleRefsUsed.length
    ? styleRefsUsed.map((ref) => ref.name).join(", ")
    : "inferred style from package text and prior mappings";

  return [
    `Book style: ${pkg.styleNotes.join(", ") || "warm picture-book"}.`,
    `Question: ${question}`,
    `Answer card text: ${option.text}`,
    `Scene: ${participants.scene}`,
    `Characters: ${participants.characters.join(", ") || "main story characters"}`,
    `Objects: ${participants.objects.join(", ") || "storybook props"}`,
    `Use style refs: ${refsText}.`,
    "Keep layout simple for child selection and preserve recurring character appearance.",
  ].join(" ");
}

function renderCardIllustration({ pkg, option, participants, cardIndex, model }) {
  const width = 640;
  const height = 420;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");

  const palette = derivePalette(pkg.title + model + cardIndex);

  const bg = ctx.createLinearGradient(0, 0, width, height);
  bg.addColorStop(0, palette[0]);
  bg.addColorStop(1, palette[1]);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, width, height);

  ctx.fillStyle = "rgba(255,255,255,0.3)";
  ctx.beginPath();
  ctx.ellipse(130, 90, 120, 55, 0, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = palette[2];
  ctx.beginPath();
  ctx.moveTo(0, height * 0.74);
  ctx.quadraticCurveTo(width * 0.3, height * 0.55, width * 0.62, height * 0.72);
  ctx.quadraticCurveTo(width * 0.84, height * 0.8, width, height * 0.7);
  ctx.lineTo(width, height);
  ctx.lineTo(0, height);
  ctx.closePath();
  ctx.fill();

  const actorCount = Math.max(1, participants.characters.length || 1);
  for (let i = 0; i < actorCount; i += 1) {
    const name = participants.characters[i] || `Character ${i + 1}`;
    const x = 130 + i * 190;
    const y = 255 + (i % 2) * 10;
    drawCharacter(ctx, x, y, name);
  }

  if (participants.objects.length) {
    participants.objects.slice(0, 2).forEach((obj, idx) => {
      drawObjectLabel(ctx, width - 180, 160 + idx * 40, obj);
    });
  }

  ctx.fillStyle = "rgba(24,28,33,0.7)";
  ctx.fillRect(18, height - 86, width - 36, 62);
  ctx.fillStyle = "#f8f3de";
  ctx.font = "bold 27px 'Trebuchet MS', sans-serif";
  ctx.fillText(truncate(option.text, 48), 32, height - 47);

  return canvas.toDataURL("image/png");
}

function drawCharacter(ctx, x, y, name) {
  const tones = ["#f8d4a1", "#d9b28a", "#c2866b", "#8b6f58"];
  const color = tones[Math.abs(hashString(name)) % tones.length];

  ctx.fillStyle = "rgba(25,33,32,0.2)";
  ctx.beginPath();
  ctx.ellipse(x + 3, y + 68, 40, 10, 0, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, 24, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = "#32443f";
  ctx.fillRect(x - 21, y + 26, 42, 44);

  ctx.fillStyle = "#f2f5f4";
  ctx.font = "bold 13px 'Trebuchet MS', sans-serif";
  ctx.fillText(truncate(name, 11), x - 32, y + 87);
}

function drawObjectLabel(ctx, x, y, text) {
  ctx.fillStyle = "rgba(253,250,240,0.87)";
  ctx.fillRect(x - 8, y - 17, 156, 30);
  ctx.fillStyle = "#17322e";
  ctx.font = "bold 16px 'Trebuchet MS', sans-serif";
  ctx.fillText(truncate(text, 18), x, y + 4);
}

function renderRunResult(result) {
  const timings = {
    ...result.telemetry.stepTimings,
    perCard: result.cards.map((card, idx) => ({ id: `card-${idx + 1}`, ...card.cardTiming })),
  };

  el.timingsView.textContent = JSON.stringify(timings, null, 2);
  el.timelineView.textContent = result.telemetry.timeline
    .map(
      (entry) =>
        `${entry.lane.padEnd(7)} | +${entry.startMs.toString().padStart(5)}ms | ${entry.durationMs
          .toString()
          .padStart(5)}ms | ${entry.event}`
    )
    .join("\n");

  el.cards.innerHTML = "";
  result.cards.forEach((card) => {
    const node = el.cardTemplate.content.firstElementChild.cloneNode(true);
    const image = node.querySelector(".card__media");
    const text = node.querySelector(".card__text");
    const badge = node.querySelector(".card__badge");

    image.src = card.imageDataUrl;
    text.textContent = card.text;
    badge.textContent = "Tap to select";

    node.dataset.correct = String(card.isCorrect);
    node.addEventListener("click", () => {
      Array.from(el.cards.children).forEach((cardNode) => {
        cardNode.querySelector(".card__badge").textContent = "Tap to select";
      });
      badge.textContent = card.isCorrect ? "Selected (book-supported answer)" : "Selected";
    });

    el.cards.appendChild(node);
  });

  el.debugView.textContent = JSON.stringify(result.debugBundle, null, 2);
}

function clearRunSurface() {
  el.timingsView.textContent = "No run yet.";
  el.timelineView.textContent = "No run yet.";
  el.cards.innerHTML = "";
  el.debugView.textContent = "No run yet.";
  state.run.latestDebugBundle = null;
}

function setRunError(message) {
  el.timingsView.textContent = message;
}

function renderPackageSelect(preferredId = "") {
  const packages = state.packages;
  el.packageSelect.innerHTML = "";

  if (!packages.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No story package yet";
    el.packageSelect.appendChild(option);
    return;
  }

  packages.forEach((pkg) => {
    const option = document.createElement("option");
    option.value = pkg.id;
    option.textContent = `${pkg.title} (${pkg.characters.length} chars)`;
    if (preferredId && preferredId === pkg.id) {
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
    node.querySelector(
      ".libraryItem__meta"
    ).textContent = `Facts: ${pkg.facts.length} | Characters: ${pkg.characters.length} | Refs: ${pkg.styleRefs.length} | Updated: ${new Date(
      pkg.updatedAt
    ).toLocaleString()}`;

    node.querySelector(".js-open").addEventListener("click", () => {
      loadPackageIntoSetup(pkg.id);
      activateTab("setup");
    });

    node.querySelector(".js-select").addEventListener("click", () => {
      renderPackageSelect(pkg.id);
      activateTab("ask");
    });

    node.querySelector(".js-delete").addEventListener("click", () => {
      state.packages = state.packages.filter((entry) => entry.id !== pkg.id);
      persistPackages();
      renderPackageSelect();
      renderLibrary();
      if (state.editingPackageId === pkg.id) {
        resetSetupForm();
      }
    });

    el.libraryList.appendChild(node);
  });
}

function activateTab(name) {
  const targetTab = el.tabs.find((tab) => tab.dataset.tab === name);
  if (targetTab) {
    targetTab.click();
  }
}

function loadPackageIntoSetup(pkgId) {
  const pkg = state.packages.find((entry) => entry.id === pkgId);
  if (!pkg) {
    return;
  }

  state.editingPackageId = pkg.id;
  state.setup.learned = {
    facts: pkg.facts,
    scenes: pkg.scenes,
    characters: pkg.characters,
    objects: pkg.objects,
    styleNotes: pkg.styleNotes,
  };
  state.setup.uploadedStyleRefs = pkg.styleRefs;

  el.storyTitle.value = pkg.title;
  el.bookText.value = pkg.rawText;
  setSetupNote(`Loaded package for editing: ${pkg.title}`);
}

function resetSetupForm() {
  state.editingPackageId = null;
  state.setup.learned = null;
  state.setup.uploadedStyleRefs = [];

  el.storyTitle.value = "";
  el.bookText.value = "";
  el.bookFile.value = "";
  el.styleRefs.value = "";
}

function setSetupNote(message) {
  el.learnSummary.textContent = message;
}

function analyzeBookText(text) {
  const cleaned = text.replace(/\s+/g, " ").trim();
  const rawSentences = cleaned
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);

  const facts = rawSentences.map((sentence) => truncate(sentence, 160)).slice(0, 24);
  const characters = extractCharacters(cleaned);
  const objects = extractObjects(cleaned, characters);
  const scenes = extractScenes(rawSentences);

  const styleWords = [
    "warm",
    "gentle",
    "storybook",
    "painted",
    "whimsical",
    "soft",
    "pastel",
    "bright",
  ];

  const styleNotes = styleWords.filter((word) => cleaned.toLowerCase().includes(word)).slice(0, 4);
  if (!styleNotes.length) {
    styleNotes.push("storybook", "friendly");
  }

  return {
    facts,
    characters: characters.slice(0, 8),
    objects: objects.slice(0, 12),
    scenes: scenes.slice(0, 8),
    styleNotes,
  };
}

function extractCharacters(text) {
  const banned = new Set([
    "The",
    "A",
    "An",
    "And",
    "But",
    "Then",
    "When",
    "After",
    "Before",
    "In",
    "On",
    "At",
    "He",
    "She",
    "They",
    "It",
    "We",
    "I",
  ]);

  const counts = new Map();
  const matches = text.match(/\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b/g) || [];
  matches.forEach((token) => {
    if (banned.has(token)) {
      return;
    }
    counts.set(token, (counts.get(token) || 0) + 1);
  });

  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([name]) => name);
}

function extractObjects(text, characters) {
  const stop = new Set([
    "that",
    "this",
    "with",
    "from",
    "were",
    "they",
    "there",
    "their",
    "then",
    "into",
    "about",
    "would",
    "could",
    "should",
    "after",
    "before",
    "because",
    "through",
    "little",
    "again",
    "once",
    "upon",
    "very",
    "more",
    "some",
    "what",
    "where",
    "when",
    "which",
    "while",
    "also",
    "have",
    "has",
    "had",
  ]);

  characters.forEach((name) => stop.add(name.toLowerCase()));
  const counts = new Map();

  const words = text.toLowerCase().match(/[a-z]{4,}/g) || [];
  words.forEach((word) => {
    if (stop.has(word)) {
      return;
    }
    counts.set(word, (counts.get(word) || 0) + 1);
  });

  return Array.from(counts.entries())
    .filter(([, count]) => count > 1)
    .sort((a, b) => b[1] - a[1])
    .map(([word]) => word);
}

function extractScenes(sentences) {
  const scenes = [];
  const locationRegex = /\b(in|at|on|near|inside|outside|by)\b([^.!?,;]+)/i;

  sentences.forEach((sentence) => {
    const match = sentence.match(locationRegex);
    if (match) {
      scenes.push(capitalize((`${match[1]} ${match[2]}`).trim()));
    }
  });

  if (!scenes.length && sentences[0]) {
    scenes.push("Main story setting");
  }

  return dedupe(scenes);
}

function scoreFactAgainstQuestion(fact, question) {
  const qWords = tokenize(question);
  const fWords = new Set(tokenize(fact));
  let score = 0;
  qWords.forEach((word) => {
    if (fWords.has(word)) {
      score += 2;
    }
  });

  const q = question.toLowerCase();
  if (q.includes("who") && /\b(he|she|they|[A-Z][a-z]+)/.test(fact)) {
    score += 1;
  }
  if (q.includes("where") && /\b(in|at|on|near|inside|outside|by)\b/i.test(fact)) {
    score += 1;
  }

  return score;
}

function answerFromFact(question, fact, pkg) {
  const lower = question.toLowerCase();

  if (lower.includes("who")) {
    const matches = pkg.characters.filter((name) => fact.toLowerCase().includes(name.toLowerCase()));
    if (matches.length) {
      return matches[0];
    }
  }

  if (lower.includes("where")) {
    const location = fact.match(/\b(in|at|on|near|inside|outside|by)\b([^.!?,;]+)/i);
    if (location) {
      return capitalize((`${location[1]} ${location[2]}`).trim());
    }
  }

  if (lower.includes("what")) {
    const object = pkg.objects.find((item) => fact.toLowerCase().includes(item.toLowerCase()));
    if (object) {
      return capitalize(object);
    }
  }

  const words = fact.replace(/[^a-zA-Z0-9\s]/g, "").split(/\s+/).filter(Boolean);
  return capitalize(words.slice(0, 8).join(" "));
}

function buildSyntheticDistractor(pkg, correctAnswer, currentDistractors) {
  const candidates = [];
  candidates.push(...pkg.characters);
  candidates.push(...pkg.objects.map(capitalize));
  candidates.push("A different part of the story");
  candidates.push("Someone else");
  candidates.push("Another place");

  for (const candidate of candidates) {
    const normalized = normalize(candidate);
    const clash = [correctAnswer, ...currentDistractors].some((existing) => normalize(existing) === normalized);
    if (!clash && normalized.length > 2) {
      return candidate;
    }
  }

  return `Not ${correctAnswer}`;
}

function derivePalette(seedInput) {
  const seed = Math.abs(hashString(seedInput));
  const h1 = seed % 360;
  const h2 = (h1 + 48) % 360;
  const h3 = (h1 + 210) % 360;

  return [
    `hsl(${h1} 70% 76%)`,
    `hsl(${h2} 64% 64%)`,
    `hsl(${h3} 35% 44%)`,
  ];
}

function loadPackages() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    state.packages = Array.isArray(parsed) ? parsed : [];
  } catch {
    state.packages = [];
  }
}

function persistPackages() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.packages));
}

function tokenize(text) {
  return (text.toLowerCase().match(/[a-z]{3,}/g) || []).filter((word) => !STOP_WORDS.has(word));
}

function normalize(text) {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

function dedupe(values) {
  return Array.from(new Set(values));
}

function truncate(text, maxLength) {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
}

function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomInt(max) {
  return Math.floor(Math.random() * max);
}

function hashString(input) {
  let hash = 0;
  for (let i = 0; i < input.length; i += 1) {
    hash = (hash << 5) - hash + input.charCodeAt(i);
    hash |= 0;
  }
  return hash;
}

function roundMs(value) {
  return Math.max(0, Math.round(value));
}

function shuffle(values) {
  const out = values.slice();
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Failed to read file"));
    reader.readAsDataURL(file);
  });
}

async function extractTextFromPdf(file) {
  const arrayBuffer = await file.arrayBuffer();

  try {
    const extractedByPdfJs = await extractTextFromPdfUsingPdfJs(arrayBuffer);
    if (extractedByPdfJs.text.length >= 40) {
      return extractedByPdfJs;
    }
  } catch {
    // Continue to heuristic fallback.
  }

  const heuristicText = extractTextFromPdfHeuristic(arrayBuffer);
  return {
    text: heuristicText,
    pageCount: 1,
    method: "heuristic",
  };
}

async function extractTextFromPdfUsingPdfJs(arrayBuffer) {
  const pdfJs = await import("https://cdn.jsdelivr.net/npm/pdfjs-dist@4.6.82/build/pdf.min.mjs");
  if (pdfJs.GlobalWorkerOptions) {
    pdfJs.GlobalWorkerOptions.workerSrc =
      "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.6.82/build/pdf.worker.min.mjs";
  }

  const loadingTask = pdfJs.getDocument({ data: arrayBuffer });
  const pdfDoc = await loadingTask.promise;
  const pages = [];

  for (let pageNum = 1; pageNum <= pdfDoc.numPages; pageNum += 1) {
    const page = await pdfDoc.getPage(pageNum);
    const textContent = await page.getTextContent();
    const pageText = textContent.items
      .map((item) => {
        if (typeof item.str === "string") {
          return item.str;
        }
        return "";
      })
      .join(" ");
    pages.push(`[Page ${pageNum}] ${pageText}`);
  }

  return {
    text: cleanExtractedText(pages.join("\n\n")),
    pageCount: pdfDoc.numPages,
    method: "pdf.js",
  };
}

function extractTextFromPdfHeuristic(arrayBuffer) {
  const source = new TextDecoder("latin1").decode(new Uint8Array(arrayBuffer));
  const chunks = [];

  const simpleTextOps = source.matchAll(/\(([^()]{2,260})\)\s*Tj/g);
  for (const match of simpleTextOps) {
    chunks.push(decodePdfLiteralString(match[1]));
  }

  const arrayTextOps = source.matchAll(/\[(.*?)\]\s*TJ/gs);
  for (const match of arrayTextOps) {
    const body = match[1];
    const inner = body.matchAll(/\(([^()]*)\)/g);
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

const STOP_WORDS = new Set([
  "about",
  "after",
  "again",
  "also",
  "because",
  "been",
  "before",
  "being",
  "came",
  "come",
  "could",
  "does",
  "down",
  "each",
  "even",
  "from",
  "have",
  "into",
  "just",
  "like",
  "many",
  "more",
  "most",
  "much",
  "must",
  "only",
  "other",
  "over",
  "same",
  "some",
  "such",
  "than",
  "that",
  "their",
  "them",
  "then",
  "there",
  "these",
  "they",
  "this",
  "those",
  "through",
  "very",
  "what",
  "when",
  "where",
  "which",
  "while",
  "with",
  "would",
]);
