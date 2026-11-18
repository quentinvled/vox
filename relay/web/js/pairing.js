// Appairage : le code affiché par Vox ouvre https://vox.vled.fr/#<salon>.<jeton>.
//
// Le fragment (#…) ne quitte jamais le téléphone : le navigateur ne l'envoie pas
// au serveur. Il est aussi conservé dans l'adresse, car sur iPhone l'icône de
// l'écran d'accueil reprend l'adresse courante mais pas le stockage de Safari.

const STORAGE_KEY = "vox.pairing";
const PATTERN = /^([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{32,128})$/;

function read() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    return value && PATTERN.test(`${value.room}.${value.token}`) ? value : null;
  } catch {
    return null;
  }
}

function store(pairing) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(pairing));
  } catch {
    // Navigation privée : l'adresse suffit pour cette session.
  }
}

export function loadPairing() {
  const match = PATTERN.exec(location.hash.slice(1));
  if (match) {
    const pairing = { room: match[1], token: match[2] };
    store(pairing);
    return pairing;
  }
  const stored = read();
  if (stored) {
    history.replaceState(null, "", `#${stored.room}.${stored.token}`);
  }
  return stored;
}

// Texte lu dans un code QR : un lien Vox de ce même relais, ou rien.
export function pairingFromText(text) {
  let url;
  try {
    url = new URL(String(text).trim());
  } catch {
    return null;
  }
  if (url.origin !== location.origin) return null;
  const match = PATTERN.exec(url.hash.slice(1));
  return match ? { room: match[1], token: match[2] } : null;
}

// Nouvel appairage (scanner intégré) : l'ordinateur précédent est remplacé.
export function usePairing(pairing) {
  store(pairing);
  try {
    localStorage.removeItem(COMPUTER_KEY);
  } catch {
    // Rien à effacer.
  }
  history.replaceState(null, "", `#${pairing.room}.${pairing.token}`);
  location.reload();
}

// Dernier état connu de l'ordinateur : affiché dès l'ouverture, avant même
// que la connexion soit établie.
const COMPUTER_KEY = "vox.computer";

export function cachedComputer() {
  try {
    const value = JSON.parse(localStorage.getItem(COMPUTER_KEY) || "null");
    return value && typeof value.name === "string" ? value : null;
  } catch {
    return null;
  }
}

export function rememberComputer(computer) {
  try {
    localStorage.setItem(COMPUTER_KEY, JSON.stringify({ name: computer.name, online: computer.online }));
  } catch {
    // Sans stockage, l'affichage attendra simplement la connexion.
  }
}

export function forgetPairing() {
  try {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(COMPUTER_KEY);
  } catch {
    // Rien à effacer.
  }
  history.replaceState(null, "", location.pathname);
}

// Nom présenté à l'ordinateur (« iPhone », « Pixel 8 »…). La version
// immédiate sert à se connecter sans attendre ; la précise est gardée pour la
// fois suivante.
const NAME_KEY = "vox.deviceName";

export function quickDeviceName() {
  return localStorage.getItem(NAME_KEY) || guessName();
}

export async function refreshDeviceName() {
  const name = await deviceName();
  try {
    localStorage.setItem(NAME_KEY, name);
  } catch {
    // Pas grave : le nom sera recalculé.
  }
}

function guessName() {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad|Macintosh/.test(ua) && navigator.maxTouchPoints > 1) return "iPad";
  if (/Android/.test(ua)) return "Téléphone Android";
  return "Téléphone";
}

async function deviceName() {
  if (navigator.userAgentData?.getHighEntropyValues) {
    try {
      const { model } = await navigator.userAgentData.getHighEntropyValues(["model"]);
      if (model) return model;
    } catch {
      // Valeur refusée : on garde le nom générique.
    }
  }
  return guessName();
}
