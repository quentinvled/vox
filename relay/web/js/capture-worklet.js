// Capture du micro dans le fil audio : ramène le signal à 16 kHz mono en
// entiers 16 bits (le format qu'attend Vox) et l'envoie par paquets de 80 ms.
// Le niveau sonore est publié environ 30 fois par seconde pour l'animation.

const TARGET_RATE = 16000;
const CHUNK = 1280; // 80 ms

class VoxCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.active = false;
    this.reset();
    this.port.onmessage = ({ data }) => {
      if (data === "start") {
        this.reset();
        this.active = true;
      } else if (data === "stop") {
        this.flush();
        this.active = false;
        this.port.postMessage({ type: "end" });
      } else if (data === "cancel") {
        this.active = false;
        this.reset();
      }
    };
  }

  reset() {
    this.out = new Int16Array(CHUNK);
    this.count = 0;
    this.sum = 0;
    this.taken = 0;
    this.phase = 0;
    this.energy = 0;
    this.frames = 0;
  }

  emit(samples) {
    this.port.postMessage({ type: "chunk", pcm: samples.buffer }, [samples.buffer]);
  }

  flush() {
    if (this.count > 0) {
      this.emit(this.out.slice(0, this.count));
    }
    this.out = new Int16Array(CHUNK);
    this.count = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || !this.active) {
      return true;
    }
    for (let i = 0; i < channel.length; i++) {
      const x = channel[i];
      this.energy += x * x;
      // Décimation par moyenne : chaque échantillon de sortie est la moyenne
      // des échantillons d'entrée qu'il couvre (filtre anti-repliement simple,
      // suffisant pour la parole).
      this.sum += x;
      this.taken += 1;
      this.phase += 1;
      if (this.phase >= this.ratio) {
        this.phase -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.sum / this.taken));
        this.sum = 0;
        this.taken = 0;
        this.out[this.count++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        if (this.count === CHUNK) {
          this.emit(this.out);
          this.out = new Int16Array(CHUNK);
          this.count = 0;
        }
      }
    }
    this.frames += channel.length;
    if (this.frames >= sampleRate / 30) {
      this.port.postMessage({ type: "level", rms: Math.sqrt(this.energy / this.frames) });
      this.energy = 0;
      this.frames = 0;
    }
    return true;
  }
}

registerProcessor("vox-capture", VoxCapture);
