// Service worker : la page s'ouvre instantanément depuis le cache, puis se
// met à jour en arrière-plan (la nouvelle version sert à l'ouverture suivante).
// Le canal /ws et /healthz ne passent jamais par le cache.

const CACHE = "vox-1.0.0";
const SHELL = [
  "/",
  "/app.css",
  "/js/main.js",
  "/js/link.js",
  "/js/mic.js",
  "/js/pairing.js",
  "/js/capture-worklet.js",
  "/manifest.webmanifest",
  "/icons/icon.svg",
  "/icons/icon-192.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
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
  const key = request.mode === "navigate" ? "/" : url.pathname;
  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(key);
      const network = fetch(request)
        .then((response) => {
          if (response.ok) cache.put(key, response.clone());
          return response;
        })
        .catch(() => cached);
      if (cached) {
        event.waitUntil(network);
        return cached;
      }
      return network;
    }),
  );
});
