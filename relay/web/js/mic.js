// Micro « chaud » : ouvert dès l'ouverture de l'app quand il est déjà autorisé
// (sinon au premier appui), gardé tant que la page sert, pour que chaque prise
// démarre sans délai. Libéré quand la page passe en arrière-plan ou après
// quelques minutes sans usage.

const IDLE_RELEASE_MS = 3 * 60 * 1000;

export class MicError extends Error {
  constructor(kind, message) {
    super(message);
    this.kind = kind; // "denied" | "unavailable" | "unsupported"
  }
}

export class Mic {
  constructor({ onChunk, onLevel }) {
    this.onChunk = onChunk;
    this.onLevel = onLevel;
    this.context = null;
    this.stream = null;
    this.node = null;
    this.capturing = false;
    this.pending = null;
    this.ending = null;
    this.idleTimer = 0;
    this.generation = 0;
  }

  get ready() {
    return Boolean(this.node && this.stream && this.stream.active);
  }

  // Ouvre le flux du micro sans geste de l'utilisateur (autorisation déjà
  // donnée) : le premier appui n'aura plus qu'à brancher le son.
  warm() {
    if (this.stream?.active || this.pendingStream) return;
    this.pendingStream = this.acquire()
      .catch(() => {})
      .finally(() => {
        this.pendingStream = null;
      });
    this.armIdle();
  }

  // À appeler pendant un geste de l'utilisateur : iOS n'autorise le son
  // qu'à cette condition. Le premier appel peut afficher la demande d'accès.
  prepare() {
    if (!navigator.mediaDevices || !window.AudioWorkletNode) {
      return Promise.reject(new MicError("unsupported", "Navigateur trop ancien pour le micro."));
    }
    if (!this.context || this.context.state === "closed") {
      this.context = new AudioContext({ latencyHint: "interactive" });
    }
    const resumed = this.context.resume();
    if (this.ready) {
      return resumed;
    }
    this.pending ??= this.open().finally(() => {
      this.pending = null;
    });
    return Promise.all([resumed, this.pending]);
  }

  async open() {
    const generation = this.generation;
    if (this.pendingStream) await this.pendingStream;
    if (generation !== this.generation) throw new MicError("unavailable", "Micro libéré.");
    if (!this.stream?.active) await this.acquire();
    await this.build();
  }

  async acquire() {
    const generation = this.generation;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (error) {
      const denied = error && (error.name === "NotAllowedError" || error.name === "SecurityError");
      throw new MicError(denied ? "denied" : "unavailable", error && error.message);
    }
    if (generation !== this.generation) {
      for (const track of stream.getTracks()) track.stop();
      throw new MicError("unavailable", "Micro libéré.");
    }
    for (const track of stream.getTracks()) {
      track.addEventListener("ended", () => this.release());
    }
    this.stream = stream;
  }

  async build() {
    const stream = this.stream;
    const context = this.context;
    if (!this.moduleLoaded) {
      await context.audioWorklet.addModule("/js/capture-worklet.js");
      if (this.context !== context) throw new MicError("unavailable", "Micro libéré.");
      this.moduleLoaded = true;
    }
    const source = context.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(context, "vox-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      channelCount: 1,
    });
    // Sortie muette : certains navigateurs ne font tourner un nœud que s'il
    // est relié à la destination.
    const mute = context.createGain();
    mute.gain.value = 0;
    source.connect(node).connect(mute).connect(context.destination);

    node.port.onmessage = ({ data }) => {
      if (data.type === "chunk") {
        if (this.capturing || this.ending) this.onChunk(data.pcm);
      } else if (data.type === "level") {
        this.onLevel(Math.min(1, data.rms * 7));
      } else if (data.type === "end" && this.ending) {
        const done = this.ending;
        this.ending = null;
        done();
      }
    };
    this.node = node;
  }

  start() {
    clearTimeout(this.idleTimer);
    this.capturing = true;
    this.node.port.postMessage("start");
  }

  // Résout quand le dernier paquet est parti.
  stop() {
    if (!this.capturing) return Promise.resolve();
    this.capturing = false;
    this.armIdle();
    return new Promise((resolve) => {
      this.ending = resolve;
      this.node.port.postMessage("stop");
      setTimeout(() => {
        if (this.ending === resolve) {
          this.ending = null;
          resolve();
        }
      }, 400);
    });
  }

  cancel() {
    if (!this.capturing) return;
    this.capturing = false;
    this.node?.port.postMessage("cancel");
    this.armIdle();
  }

  armIdle() {
    clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => this.release(), IDLE_RELEASE_MS);
  }

  release() {
    this.generation += 1;
    clearTimeout(this.idleTimer);
    this.capturing = false;
    this.ending = null;
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
    }
    this.node?.disconnect();
    this.stream = null;
    this.node = null;
    if (this.context && this.context.state !== "closed") {
      this.context.close().catch(() => {});
    }
    this.context = null;
    this.moduleLoaded = false;
  }
}

// État de l'autorisation sans la demander (« granted », « prompt », « denied »
// ou « unknown » quand le navigateur ne sait pas le dire).
export async function micPermission() {
  try {
    const status = await navigator.permissions.query({ name: "microphone" });
    return status.state;
  } catch {
    return "unknown";
  }
}
