// Liaison avec l'ordinateur à travers le relais (WebSocket).
//
// Événements émis (CustomEvent, détail entre parenthèses) :
//   status   ({ link, computer })  link : "connecting" | "open" | "offline"
//   message  (objet JSON reçu de l'ordinateur)
//   revoked  ()                    le relais refuse ce téléphone

const CLOSE_NOT_ALLOWED = 4003;
const MAX_QUEUE_BYTES = 4 * 1024 * 1024;

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
  }

  get online() {
    return this.ready && this.computer.online;
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

    socket.addEventListener("open", () => {
      socket.send(
        JSON.stringify({ t: "auth", role: "phone", room: this.room, token: this.token, name: this.name }),
      );
    });
    socket.addEventListener("message", (event) => {
      if (typeof event.data !== "string") return;
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      this.receive(message);
    });
    socket.addEventListener("close", (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.ready = false;
      if (event.code === CLOSE_NOT_ALLOWED) {
        this.stopped = true;
        this.dispatchEvent(new CustomEvent("revoked"));
        return;
      }
      this.emitStatus(navigator.onLine === false ? "offline" : "connecting");
      this.scheduleRetry();
    });
  }

  receive(message) {
    if (message.t === "ready") {
      this.ready = true;
      this.retry = 0;
      this.computer = message.computer;
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
  wake() {
    if (this.stopped) return;
    this.retry = 0;
    if (!this.socket || this.socket.readyState > WebSocket.OPEN) {
      this.connect();
    }
  }

  close() {
    this.stopped = true;
    clearTimeout(this.retryTimer);
    this.dropQueue();
    this.socket?.close(1000);
    this.socket = null;
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
