// Scanner de code QR intégré : relier l'app installée sans passer par
// l'appareil photo, qui ouvrirait le lien dans le navigateur et non dans l'app.
//
// BarcodeDetector quand le navigateur l'offre (Android), sinon jsQR dans un
// worker. Seule la zone du viseur est analysée, environ 8 fois par seconde.

const SAMPLE = 480; // côté de l'image analysée, en pixels
const INTERVAL_MS = 120;

export class ScanError extends Error {
  constructor(kind) {
    super(kind);
    this.kind = kind; // "denied" | "unavailable"
  }
}

export class Scanner {
  constructor(video, onText) {
    this.video = video;
    this.onText = onText;
    this.stream = null;
    this.detector = null;
    this.worker = null;
    this.canvas = null;
    this.running = false;
    this.closed = false;
    this.timer = 0;
  }

  async start() {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
      });
    } catch (error) {
      const denied = error && (error.name === "NotAllowedError" || error.name === "SecurityError");
      throw new ScanError(denied ? "denied" : "unavailable");
    }
    if (this.closed) {
      for (const track of stream.getTracks()) track.stop();
      return;
    }
    this.stream = stream;
    this.video.srcObject = stream;
    await this.video.play();

    if ("BarcodeDetector" in window) {
      try {
        const formats = await window.BarcodeDetector.getSupportedFormats();
        if (formats.includes("qr_code")) this.detector = new window.BarcodeDetector({ formats: ["qr_code"] });
      } catch {
        this.detector = null;
      }
    }
    if (this.closed) return;
    if (!this.detector) {
      this.worker = new Worker("/js/scan-worker.js");
      this.canvas = document.createElement("canvas");
      this.canvas.width = this.canvas.height = SAMPLE;
    }
    this.running = true;
    this.schedule();
  }

  schedule() {
    if (this.running) this.timer = setTimeout(() => this.tick(), INTERVAL_MS);
  }

  async tick() {
    const video = this.video;
    if (!this.running) return;
    if (video.readyState < 2 || !video.videoWidth) return this.schedule();
    try {
      const text = this.detector ? await this.detect() : await this.decode();
      if (text && this.running) this.onText(text);
    } catch {
      // Image illisible : on réessaie à la suivante.
    }
    this.schedule();
  }

  async detect() {
    const codes = await this.detector.detect(this.video);
    return codes.length ? codes[0].rawValue : null;
  }

  // Carré central de l'image (là où se trouve le viseur), réduit à SAMPLE px.
  decode() {
    const { videoWidth: w, videoHeight: h } = this.video;
    const side = Math.min(w, h) * 0.8;
    const context = this.canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(this.video, (w - side) / 2, (h - side) / 2, side, side, 0, 0, SAMPLE, SAMPLE);
    const { data } = context.getImageData(0, 0, SAMPLE, SAMPLE);
    return new Promise((resolve) => {
      this.worker.onmessage = ({ data: text }) => resolve(text);
      this.worker.postMessage({ width: SAMPLE, height: SAMPLE, pixels: data.buffer }, [data.buffer]);
    });
  }

  stop() {
    this.closed = true;
    this.running = false;
    clearTimeout(this.timer);
    this.worker?.terminate();
    this.worker = null;
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
    }
    this.stream = null;
    this.video.srcObject = null;
  }
}
