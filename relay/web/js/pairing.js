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

export function loadPairing() {
  const match = PATTERN.exec(location.hash.slice(1));
  if (match) {
    const pairing = { room: match[1], token: match[2] };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(pairing));
    } catch {
      // Navigation privée : l'adresse suffit pour cette session.
    }
    return pairing;
  }
  const stored = read();
  if (stored) {
    history.replaceState(null, "", `#${stored.room}.${stored.token}`);
  }
  return stored;
}

export function forgetPairing() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Rien à effacer.
  }
  history.replaceState(null, "", location.pathname);
}

// Nom présenté à l'ordinateur (« iPhone », « Pixel 8 »…).
export async function deviceName() {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad|Macintosh/.test(ua) && navigator.maxTouchPoints > 1) return "iPad";
  if (navigator.userAgentData?.getHighEntropyValues) {
    try {
      const { model } = await navigator.userAgentData.getHighEntropyValues(["model"]);
      if (model) return model;
    } catch {
      // Valeur refusée : on garde le nom générique.
    }
  }
  if (/Android/.test(ua)) return "Téléphone Android";
  return "Téléphone";
}
