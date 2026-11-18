// Vox sur le téléphone : maintenir pour parler, relâcher pour envoyer.
//
// Gestes du bouton :
//   appui long        parle tant que le doigt reste posé ;
//   glisser vers le haut  bascule dictée ⇄ consigne pendant l'appui ;
//   appui bref        écoute libre, un nouvel appui envoie.

import { Link } from "./link.js";
import { Mic, micPermission } from "./mic.js";
import { deviceName, forgetPairing, loadPairing } from "./pairing.js";

const VERSION = "1.0.0";
const TAP_MS = 280;
const SWIPE_PX = 56;
const WORK_TIMEOUT_MS = 60000;

const $ = (selector) => document.querySelector(selector);
const body = document.body;
const ui = {
  talk: $("#talk"),
  bars: [...document.querySelectorAll(".bars i")],
  hint: $("#hint"),
  cancel: $("#cancel"),
  cue: $("#cue-text"),
  transcript: $("#transcript"),
  actions: $("#actions"),
  modes: [...document.querySelectorAll(".modes button")],
  deviceName: $("#device-name"),
  deviceStatus: $("#device-status"),
  menu: $("#menu-button"),
  sheet: $("#sheet"),
  sheetDevice: $("#sheet-device"),
  sheetInstall: $("#sheet-install"),
  sheetInstallNote: $("#sheet-install-note"),
  sheetUnpair: $("#sheet-unpair"),
  sheetUnpairLabel: $("#sheet-unpair-label"),
  sheetMeta: $("#sheet-meta"),
  banner: $("#install-banner"),
  bannerText: $("#install-text"),
  bannerAction: $("#install-action"),
  bannerClose: $("#install-close"),
  welcomeLead: $("#welcome-lead"),
};

const LABELS = {
  dictation: "Dictée",
  instruction: "Consigne",
};

const WORKING = {
  transcribing: "Transcription…",
  rewording: "Mise au propre…",
  instructing: "Rédaction…",
};

const state = {
  baseMode: "dictation", // choisi avec le sélecteur
  mode: "dictation", // mode de la prise en cours
  phase: "idle",
  capture: null,
  micAllowed: false,
  link: null,
  mic: null,
  timers: { hint: 0, work: 0, clock: 0, unpair: 0 },
  installPrompt: null,
  wakeLock: null,
};

// ---------------------------------------------------------------------
// Affichage
// ---------------------------------------------------------------------
function setPhase(phase, hint, { quietAfter = 0 } = {}) {
  state.phase = phase;
  body.dataset.phase = phase;
  setHint(hint ?? idleHint());
  clearTimeout(state.timers.hint);
  if (quietAfter) {
    state.timers.hint = setTimeout(() => {
      if (state.phase === phase) {
        if (phase === "error") setPhase(restingPhase());
        else setHint(idleHint(), true);
      }
    }, quietAfter);
  }
}

function setHint(text, quiet = false) {
  ui.hint.textContent = text;
  ui.hint.classList.toggle("is-quiet", quiet);
}

function restingPhase() {
  return state.link?.online ? "idle" : "offline";
}

function idleHint() {
  const link = state.link;
  if (!link || !link.ready) return "Connexion…";
  if (!link.computer.online) return "Ouvre Vox sur ton ordinateur";
  if (!state.micAllowed) return "Touche pour activer le micro";
  return "Maintiens pour parler";
}

function settle() {
  if (state.capture) return;
  if (["working", "done", "review", "error"].includes(state.phase) && state.link?.online) {
    if (state.phase !== "working") setHint(idleHint(), state.phase === "done");
    return;
  }
  setPhase(restingPhase());
}

function setMode(mode) {
  state.mode = mode;
  body.dataset.mode = mode;
  for (const button of ui.modes) {
    button.setAttribute("aria-checked", String(button.dataset.mode === mode));
  }
  ui.cue.textContent = LABELS[mode === "dictation" ? "instruction" : "dictation"];
}

function showTranscript(text, { pending = false } = {}) {
  const value = (text || "").trim();
  if (!value) return;
  const node = ui.transcript;
  node.textContent = value;
  node.classList.remove("is-empty", "is-new");
  node.classList.toggle("is-pending", pending);
  void node.offsetWidth; // relance l'animation
  node.classList.add("is-new");
  node.scrollTop = node.scrollHeight;
}

function renderLink({ link, computer }) {
  body.dataset.link = link === "open" && computer.online ? "online" : link === "open" ? "away" : link;
  ui.deviceName.textContent = computer.name || "Ordinateur";
  ui.sheetDevice.textContent = computer.name || "l’ordinateur";
  ui.deviceStatus.textContent =
    link !== "open" ? (link === "offline" ? "Pas de réseau" : "Connexion…") : computer.online ? "Connecté" : "Hors ligne";
  settle();
}

function vibrate(pattern) {
  navigator.vibrate?.(pattern);
}

function renderLevel(level) {
  body.style.setProperty("--level", level.toFixed(3));
  const weights = [0.55, 0.82, 1, 0.8, 0.6];
  ui.bars.forEach((bar, index) => {
    const jitter = 0.8 + Math.random() * 0.4;
    const value = Math.min(1, 0.26 + level * weights[index] * jitter);
    bar.style.setProperty("--bar", value.toFixed(3));
  });
}

function startClock(capture) {
  clearInterval(state.timers.clock);
  state.timers.clock = setInterval(() => {
    if (state.capture !== capture) return clearInterval(state.timers.clock);
    const seconds = Math.floor((performance.now() - capture.startedAt) / 1000);
    const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    setHint(capture.latched ? `Touche pour envoyer · ${clock}` : `J’écoute · ${clock}`);
  }, 500);
}

async function holdScreen(on) {
  try {
    if (on && !state.wakeLock && navigator.wakeLock) {
      state.wakeLock = await navigator.wakeLock.request("screen");
    } else if (!on && state.wakeLock) {
      await state.wakeLock.release();
      state.wakeLock = null;
    }
  } catch {
    state.wakeLock = null;
  }
}

// ---------------------------------------------------------------------
// Prise de parole
// ---------------------------------------------------------------------
function onPointerDown(event) {
  event.preventDefault();
  const current = state.capture;
  if (current) {
    if (current.latched) finish();
    return;
  }
  if (state.phase === "working") return;
  const link = state.link;
  if (!link.online) {
    setPhase(restingPhase(), link.ready ? "Ouvre Vox sur ton ordinateur" : "Connexion…");
    vibrate(30);
    return;
  }

  const capture = {
    pointerId: event.pointerId,
    startY: event.clientY,
    startedAt: performance.now(),
    latched: false,
    released: false,
    swiped: false,
    live: false,
  };
  state.capture = capture;
  ui.talk.setPointerCapture?.(event.pointerId);
  setMode(state.baseMode);
  body.dataset.holding = "true";

  const wasAllowed = state.micAllowed;
  const preparing = state.mic.prepare();
  if (state.mic.ready) {
    begin(capture);
    return;
  }
  setHint("Activation du micro…");
  preparing.then(
    () => {
      state.micAllowed = true;
      if (state.capture !== capture) return;
      if (!wasAllowed) {
        // Ce geste a servi à autoriser le micro : la prochaine prise sera la bonne.
        endCapture();
        setPhase("idle", "Micro prêt. Maintiens pour parler");
        return;
      }
      begin(capture);
    },
    (error) => {
      endCapture();
      const denied = error?.kind === "denied";
      setPhase(
        "error",
        denied ? "Micro refusé : autorise-le dans les réglages du navigateur" : "Micro indisponible",
        { quietAfter: 6000 },
      );
      vibrate([30, 60, 30]);
    },
  );
}

function begin(capture) {
  capture.live = true;
  state.link.send({ t: "start", mode: state.mode, rate: 16000 }, { queue: true });
  state.mic.start();
  vibrate(12);
  holdScreen(true);
  setPhase("listening", capture.latched ? "Touche pour envoyer" : "J’écoute…");
  startClock(capture);
}

function onPointerMove(event) {
  const capture = state.capture;
  if (!capture || capture.latched || capture.released || event.pointerId !== capture.pointerId) return;
  const rise = capture.startY - event.clientY;
  if (!capture.swiped && rise > SWIPE_PX) {
    capture.swiped = true;
    switchMode(state.mode === "dictation" ? "instruction" : "dictation");
  } else if (capture.swiped && rise < SWIPE_PX / 2) {
    capture.swiped = false;
    switchMode(state.baseMode);
  }
}

function onPointerUp(event) {
  const capture = state.capture;
  if (!capture || event.pointerId !== capture.pointerId || capture.released) return;
  capture.released = true;
  body.dataset.holding = "false";
  if (performance.now() - capture.startedAt < TAP_MS) {
    capture.latched = true;
    ui.cancel.hidden = false;
    if (capture.live) setHint("Touche pour envoyer");
    return;
  }
  finish();
}

function onPointerCancel(event) {
  const capture = state.capture;
  if (capture && event.pointerId === capture.pointerId && !capture.latched) cancel();
}

function switchMode(mode) {
  setMode(mode);
  vibrate(8);
  if (state.capture?.live) state.link.send({ t: "mode", mode }, { queue: true });
}

function endCapture() {
  state.capture = null;
  body.dataset.holding = "false";
  ui.cancel.hidden = true;
  clearInterval(state.timers.clock);
  renderLevel(0);
  holdScreen(false);
}

async function finish() {
  const capture = state.capture;
  if (!capture) return;
  endCapture();
  if (!capture.live) {
    setPhase(restingPhase());
    return;
  }
  setPhase("working", "Envoi…");
  vibrate(10);
  await state.mic.stop();
  state.link.send({ t: "stop" }, { queue: true });
  armWorkTimeout();
}

function cancel() {
  const capture = state.capture;
  if (!capture) return;
  endCapture();
  if (capture.live) {
    state.mic.cancel();
    state.link.send({ t: "cancel" }, { queue: true });
  }
  setMode(state.baseMode);
  setPhase(restingPhase(), "Annulé", { quietAfter: 1800 });
}

function armWorkTimeout() {
  clearTimeout(state.timers.work);
  state.timers.work = setTimeout(() => {
    if (state.phase === "working") {
      setPhase("error", "Pas de réponse de l’ordinateur", { quietAfter: 5000 });
    }
  }, WORK_TIMEOUT_MS);
}

// ---------------------------------------------------------------------
// Messages de l'ordinateur
// ---------------------------------------------------------------------
function onMessage({ detail: message }) {
  if (message.t === "state") onComputerState(message);
  else if (message.t === "notice" && !state.capture && state.phase !== "working") {
    setPhase(restingPhase(), message.message, { quietAfter: 3000 });
  }
}

function onComputerState({ state: status, text, message }) {
  // L'ordinateur a pu conclure seul (durée maximale atteinte) : on suit.
  if (status in WORKING || status === "busy") {
    if (state.capture?.live) {
      state.mic.cancel();
      endCapture();
    }
  }
  clearTimeout(state.timers.work);

  if (status in WORKING) {
    if (text) showTranscript(text, { pending: true });
    setPhase("working", WORKING[status]);
    armWorkTimeout();
  } else if (status === "done") {
    showTranscript(text);
    setMode(state.baseMode);
    setPhase("done", "Inséré sur l’ordinateur", { quietAfter: 3500 });
    vibrate(14);
  } else if (status === "review") {
    showTranscript(text);
    setMode(state.baseMode);
    setPhase("review", "Pas inséré : la fenêtre a changé");
    vibrate([20, 50, 20]);
  } else if (status === "busy") {
    setMode(state.baseMode);
    setPhase("error", message || "Vox est occupé sur l’ordinateur", { quietAfter: 4000 });
    vibrate([30, 60, 30]);
  } else if (status === "error") {
    setMode(state.baseMode);
    setPhase("error", message || "Problème sur l’ordinateur", { quietAfter: 5000 });
    vibrate([30, 60, 30]);
  } else if (status === "idle") {
    if (state.capture) return;
    setMode(state.baseMode);
    setPhase(restingPhase(), message || idleHint(), { quietAfter: message ? 3000 : 0 });
  }
}

function onAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const name = button.dataset.action;
  if (!state.link.send({ t: "action", name })) {
    setHint("Ordinateur hors ligne");
    return;
  }
  vibrate(10);
  button.classList.add("is-sent");
  setTimeout(() => button.classList.remove("is-sent"), 900);
  if (name === "enter") setHint("Entrée envoyée", true);
  if (name === "undo") setHint("Annulé sur l’ordinateur", true);
}

// ---------------------------------------------------------------------
// Options, installation, appairage
// ---------------------------------------------------------------------
function openSheet() {
  resetUnpair();
  ui.sheetInstall.hidden = isStandalone();
  ui.sheetInstallNote.hidden = true;
  ui.sheet.hidden = false;
  ui.sheet.classList.remove("is-closing");
}

function closeSheet() {
  ui.sheet.classList.add("is-closing");
  setTimeout(() => {
    ui.sheet.hidden = true;
    ui.sheet.classList.remove("is-closing");
  }, 200);
}

function resetUnpair() {
  clearTimeout(state.timers.unpair);
  ui.sheetUnpair.dataset.armed = "false";
  ui.sheetUnpairLabel.textContent = "Délier ce téléphone";
}

function onUnpair() {
  if (ui.sheetUnpair.dataset.armed !== "true") {
    ui.sheetUnpair.dataset.armed = "true";
    ui.sheetUnpairLabel.textContent = "Toucher encore pour confirmer";
    state.timers.unpair = setTimeout(resetUnpair, 3500);
    return;
  }
  state.link.send({ t: "unpair" });
  setTimeout(() => {
    state.link.close();
    forgetPairing();
    closeSheet();
    showWelcome("Ce téléphone n’est plus relié.");
  }, 150);
}

function isStandalone() {
  return matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
}

function isIOS() {
  return /iPhone|iPad/.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(navigator.userAgent));
}

const IOS_INSTALL = "Dans Safari : <b>Partager</b>, puis <b>Sur l’écran d’accueil</b>.";

function setupInstall() {
  if (isStandalone()) return;
  const dismissed = localStorage.getItem("vox.installDismissed") === "1";
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    state.installPrompt = event;
    if (!dismissed) {
      ui.bannerText.textContent = "Installe Vox pour l’ouvrir d’un geste.";
      ui.bannerAction.hidden = false;
      ui.banner.hidden = false;
    }
  });
  if (isIOS() && !dismissed) {
    ui.bannerText.innerHTML = `Pour l’ouvrir d’un geste : ${IOS_INSTALL.replace("Dans Safari : ", "")}`;
    ui.banner.hidden = false;
  }
  ui.bannerClose.addEventListener("click", () => {
    ui.banner.hidden = true;
    localStorage.setItem("vox.installDismissed", "1");
  });
  ui.bannerAction.addEventListener("click", promptInstall);
}

async function promptInstall() {
  const prompt = state.installPrompt;
  if (!prompt) return false;
  state.installPrompt = null;
  ui.banner.hidden = true;
  await prompt.prompt();
  return true;
}

async function onSheetInstall() {
  if (await promptInstall()) {
    closeSheet();
    return;
  }
  ui.sheetInstallNote.innerHTML = isIOS()
    ? IOS_INSTALL
    : "Dans le menu du navigateur : <b>Ajouter à l’écran d’accueil</b>.";
  ui.sheetInstallNote.hidden = false;
}

function showWelcome(alert) {
  body.dataset.screen = "welcome";
  if (alert) {
    ui.welcomeLead.textContent = alert + " Relie-le de nouveau depuis l’ordinateur.";
    ui.welcomeLead.classList.add("is-alert");
  }
}

// ---------------------------------------------------------------------
// Démarrage
// ---------------------------------------------------------------------
function wire() {
  ui.talk.addEventListener("pointerdown", onPointerDown);
  ui.talk.addEventListener("pointermove", onPointerMove);
  ui.talk.addEventListener("pointerup", onPointerUp);
  ui.talk.addEventListener("pointercancel", onPointerCancel);
  ui.talk.addEventListener("contextmenu", (event) => event.preventDefault());
  ui.talk.addEventListener("keydown", (event) => {
    if ((event.key === " " || event.key === "Enter") && !event.repeat) {
      event.preventDefault();
      if (state.capture) finish();
      else onPointerDown({ preventDefault() {}, pointerId: -1, clientY: 0 });
    }
  });
  ui.cancel.addEventListener("click", cancel);
  ui.actions.addEventListener("click", onAction);
  for (const button of ui.modes) {
    button.addEventListener("click", () => {
      state.baseMode = button.dataset.mode;
      switchMode(button.dataset.mode);
    });
  }
  ui.menu.addEventListener("click", openSheet);
  ui.sheet.addEventListener("click", (event) => {
    if (event.target.closest("[data-close]")) closeSheet();
  });
  ui.sheetUnpair.addEventListener("click", onUnpair);
  ui.sheetInstall.addEventListener("click", onSheetInstall);

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      if (state.capture) cancel();
      state.mic.release();
    } else {
      state.link?.wake();
    }
  });
  window.addEventListener("online", () => state.link?.wake());
  window.addEventListener("offline", () => renderLink({ link: "offline", computer: state.link.computer }));
}

async function start() {
  ui.sheetMeta.textContent = `Vox · version ${VERSION}`;
  setMode("dictation");
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  // Un nouveau code scanné alors que la page est déjà ouverte.
  window.addEventListener("hashchange", () => location.reload());

  const pairing = loadPairing();
  if (!pairing) {
    showWelcome();
    return;
  }

  state.micAllowed = (await micPermission()) === "granted";
  state.mic = new Mic({
    onChunk: (pcm) => state.link.send(pcm, { queue: true }),
    onLevel: renderLevel,
  });
  state.link = new Link({ ...pairing, name: await deviceName() });
  state.link.addEventListener("status", ({ detail }) => renderLink(detail));
  state.link.addEventListener("message", onMessage);
  state.link.addEventListener("revoked", () => {
    state.mic.release();
    forgetPairing();
    showWelcome("Ce téléphone n’est plus relié.");
  });

  wire();
  setupInstall();
  body.dataset.screen = "app";
  setPhase("offline", "Connexion…");
  state.link.connect();
}

start();
