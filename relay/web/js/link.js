// Liaison avec l'ordinateur à travers le relais (WebSocket).
//
// Événements émis (CustomEvent, détail entre parenthèses) :
//   status   ({ link, computer })  link : "connecting" | "open" | "offline" | "unreachable"
//   message  (objet JSON reçu de l'ordinateur)
//   revoked  ()                    le relais refuse ce téléphone

const CLOSE_NOT_ALLOWED = 4003;
const MAX_QUEUE_BYTES = 4 * 1024 * 1024;
const PROBE_MS = 1500;
const CONNECT_MS = 8000;

export class Link extends EventTarget {
  constructor({ room, token, name }) {
    super();
    this.room = room;
    this.token = token;
    this.name = name;
    this.socket = null;
    this.ready = false;
    this.computer = { online: false, name: "" };
    this.retry = 0;
    this.retryTimer = 0;
    this.queue = [];
    this.queued = 0;
    this.stopped = false;
    this.probeTimer = 0;
    this.connectTimer = 0;
  }

  get online() {
    return this.ready && this.computer.online;
  }

  // Connexion pas encore établie (ouverture de l'app, reprise) : on peut déjà
  // préparer des messages, ils partiront dès qu'elle sera prête.
  get pending() {
    return !this.stopped && !this.ready;
  }

  connect() {
    this.stopped = false;
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;
    clearTimeout(this.retryTimer);
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/ws`);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    this.ready = false;
    this.emitStatus("connecting");
    this.connectTimer = setTimeout(() => {
      if (this.socket !== socket || this.ready) return;
      this.detach();
      this.emitStatus(navigator.onLine === false ? "offline" : "unreachable");
      this.scheduleRetry();
    }, CONNECT_MS);

    socket.addEventListener("open", () => {
      if (this.socket !== socket) return;
      socket.send(
        JSON.stringify({ t: "auth", role: "phone", room: this.room, token: this.token, name: this.name }),
      );
    });
    socket.addEventListener("message", (event) => {
      if (this.socket !== socket || typeof event.data !== "string") return;
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      clearTimeout(this.probeTimer);
      this.receive(message);
    });
    socket.addEventListener("close", (event) => {
      if (this.socket !== socket) return;
      clearTimeout(this.connectTimer);
      clearTimeout(this.probeTimer);
      this.socket = null;
      this.ready = false;
      if (event.code === CLOSE_NOT_ALLOWED) {
        this.stopped = true;
        this.dispatchEvent(new CustomEvent("revoked"));
        return;
      }
      this.emitStatus(navigator.onLine === false ? "offline" : "unreachable");
      this.scheduleRetry();
    });
  }

  receive(message) {
    if (message.t === "pong") return;
    if (message.t === "ready") {
      clearTimeout(this.connectTimer);
      this.ready = true;
      this.retry = 0;
      this.computer = message.computer;
      if (!this.computer.online) this.dropQueue();
      this.emitStatus("open");
      this.flush();
    } else if (message.t === "computer") {
      this.computer = { online: message.online, name: message.name };
      this.emitStatus("open");
      if (!message.online) this.dropQueue();
      else this.flush();
    } else {
      this.dispatchEvent(new CustomEvent("message", { detail: message }));
    }
  }

  emitStatus(link) {
    this.dispatchEvent(new CustomEvent("status", { detail: { link, computer: this.computer } }));
  }

  scheduleRetry() {
    if (this.stopped) return;
    clearTimeout(this.retryTimer);
    const delay = Math.min(10000, 400 * 2 ** this.retry) * (0.8 + Math.random() * 0.4);
    this.retry = Math.min(this.retry + 1, 6);
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }

  // Retour au premier plan, réseau retrouvé : on n'attend pas le prochain essai.
  // Une connexion qui semble ouverte peut être morte (téléphone en veille) :
  // une sonde la vérifie et la remplace aussitôt si elle ne répond pas.
  wake() {
    if (this.stopped) return;
    this.retry = 0;
    const socket = this.socket;
    if (!socket || socket.readyState > WebSocket.OPEN) {
      this.connect();
      return;
    }
    if (socket.readyState !== WebSocket.OPEN || !this.ready) return;
    clearTimeout(this.probeTimer);
    socket.send(JSON.stringify({ t: "ping" }));
    this.probeTimer = setTimeout(() => {
      if (this.socket !== socket) return;
      this.reconnect();
    }, PROBE_MS);
  }

  close() {
    this.stopped = true;
    this.detach();
  }

  reconnect() {
    if (this.stopped) return;
    this.retry = 0;
    this.detach();
    this.connect();
  }

  detach() {
    clearTimeout(this.retryTimer);
    clearTimeout(this.probeTimer);
    clearTimeout(this.connectTimer);
    this.dropQueue();
    const socket = this.socket;
    this.socket = null;
    this.ready = false;
    socket?.close(1000);
  }

  // JSON ou ArrayBuffer. Pendant une prise, ce qui ne peut pas partir tout de
  // suite (reconnexion en cours) attend, dans une limite raisonnable.
  send(payload, { queue = false } = {}) {
    const data = payload instanceof ArrayBuffer ? payload : JSON.stringify(payload);
    if (this.online && this.socket?.readyState === WebSocket.OPEN && this.queue.length === 0) {
      this.socket.send(data);
      return true;
    }
    if (!queue) return false;
    const size = typeof data === "string" ? data.length : data.byteLength;
    if (this.queued + size > MAX_QUEUE_BYTES) return false;
    this.queue.push(data);
    this.queued += size;
    return true;
  }

  flush() {
    if (!this.online || this.socket?.readyState !== WebSocket.OPEN) return;
    for (const data of this.queue) this.socket.send(data);
    this.dropQueue();
  }

  dropQueue() {
    this.queue = [];
    this.queued = 0;
  }
}
