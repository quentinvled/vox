// Lecture des codes QR hors du fil principal (navigateurs sans BarcodeDetector,
// notamment Safari sur iPhone). Reçoit une image, renvoie le texte lu ou null.

importScripts("/vendor/jsqr-1.4.0.min.js");

self.onmessage = ({ data }) => {
  const { width, height, pixels } = data;
  const code = self.jsQR(new Uint8ClampedArray(pixels), width, height, {
    inversionAttempts: "attemptBoth",
  });
  self.postMessage(code ? code.data : null);
};
