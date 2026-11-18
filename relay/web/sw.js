// Service worker : la page s'ouvre instantanément depuis le cache.
//
// Chaque version de la page a son propre cache (CACHE), rempli en entier à
// l'installation, directement depuis le serveur. La nouvelle version prend la
// main d'un bloc (la page se recharge alors, voir main.js) : jamais de mélange
// entre fichiers d'anciennes et de nouvelles versions.
// À chaque modification de web/ : augmenter CACHE (et VERSION dans main.js).
// Le canal /ws et /healthz ne passent jamais par le cache.

const CACHE = "vox-1.1.2";
const SHELL = [
  "/",
  "/app.css",
  "/js/main.js?v=1.1.2",
  "/js/link.js",
  "/js/mic.js",
  "/js/pairing.js",
  "/js/capture-worklet.js",
  "/js/scanner.js",
  "/js/scan-worker.js",
  "/vendor/jsqr-1.4.0.min.js",
  "/manifest.webmanifest",
  "/icons/icon.svg",
  "/icons/icon-192.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  // « reload » : ignore le cache HTTP du navigateur (Cloudflare y autorise 4 h).
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL.map((url) => new Request(url, { cache: "reload" })))),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname === "/ws" || url.pathname === "/healthz") return;

  // Toute navigation sert la page unique (l'adresse porte le fragment d'appairage).
  const key = request.mode === "navigate" ? "/" : url.pathname + url.search;
  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(key);
      if (cached) return cached;
      const response = await fetch(key === "/" ? "/" : request.url, { cache: "no-cache" });
      if (response.ok) cache.put(key, response.clone());
      return response;
    }),
  );
});
