"""Tests de la capture du son du systeme (PyAudioWPatch simule)."""

from __future__ import annotations

import io
import sys
import types
import wave

import numpy as np
import pytest

from vox import sources
from vox.recorder import RecorderError, SystemRecorder, _resample


@pytest.fixture(autouse=True)
def _reset_probe():
    sources.reset()
    yield
    sources.reset()


def _install_fake_pyaudio(monkeypatch):
    streams: list[object] = []

    class _FakeStream:
        def __init__(self, kwargs) -> None:
            self.kwargs = kwargs
            self.started = False

        def start_stream(self) -> None:
            self.started = True

        def stop_stream(self) -> None:
            self.started = False

        def close(self) -> None:
            pass

        def feed(self, samples: np.ndarray) -> None:
            """Simule le callback PortAudio avec un bloc int16."""
            data = samples.astype("<i2").tobytes()
            self.kwargs["stream_callback"](data, len(samples), None, 0)

    class _FakePyAudio:
        def open(self, **kwargs):
            stream = _FakeStream(kwargs)
            streams.append(stream)
            return stream

        def terminate(self) -> None:
            pass

    module = types.ModuleType("pyaudiowpatch")
    module.PyAudio = _FakePyAudio
    module.paInt16 = 8
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)
    return streams


def test_system_recorder_captures_and_resamples(monkeypatch) -> None:
    streams = _install_fake_pyaudio(monkeypatch)
    recorder = SystemRecorder(
        {"index": 3, "name": "Sortie", "rate": 48000, "channels": 2}
    )
    recorder.start()
    assert streams and streams[0].started
    assert streams[0].kwargs["input_device_index"] == 3

    # 1 seconde de sinus stereo a 48 kHz (meme phase sur les deux canaux).
    time_axis = np.arange(48000, dtype=np.float32) / 48000.0
    tone = (np.sin(2 * np.pi * 440 * time_axis) * 12000).astype(np.int16)
    stereo = np.repeat(tone[:, None], 2, axis=1).reshape(-1)
    for start in range(0, stereo.size, 9600):
        streams[0].feed(stereo[start : start + 9600])

    assert recorder.level > 0
    assert recorder.has_speech
    wav = recorder.stop()
    assert not recorder.recording

    with wave.open(io.BytesIO(wav), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 16000
        assert handle.getsampwidth() == 2
        assert abs(handle.getnframes() - 16000) <= 2
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    assert np.max(np.abs(data)) > 5000  # le signal est bien la


def test_system_recorder_requires_a_device(monkeypatch) -> None:
    _install_fake_pyaudio(monkeypatch)
    recorder = SystemRecorder(None)
    with pytest.raises(RecorderError):
        recorder.start()


def test_system_recorder_reports_missing_component(monkeypatch) -> None:
    # pyaudiowpatch absent : l'ouverture doit echouer proprement.
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", None)
    recorder = SystemRecorder({"index": 0, "rate": 48000, "channels": 2})
    with pytest.raises(RecorderError):
        recorder.start()


def test_resample_preserves_duration() -> None:
    samples = np.arange(48000, dtype=np.int16)
    out = _resample(samples, 48000, 16000)
    assert abs(out.size - 16000) <= 1
    assert out.dtype == np.int16
    # Meme taux : le signal est renvoye tel quel.
    assert _resample(samples, 16000, 16000) is samples


def test_stream_resampler_is_continuous() -> None:
    from vox.recorder import StreamResampler

    resampler = StreamResampler(48000, 16000)
    time_axis = np.arange(48000, dtype=np.float32) / 48000.0
    source = (np.sin(2 * np.pi * 440 * time_axis) * 12000).astype(np.int16)

    pieces = [
        resampler.process(source[index : index + 1024])
        for index in range(0, source.size, 1024)
    ]
    out = np.concatenate(pieces)
    assert abs(out.size - 16000) <= 3
    # Continuite : la plus grande variation reste celle d'un sinus 440 Hz a
    # 16 kHz (~2070) ; une discontinuite entre blocs la ferait exploser.
    assert float(np.max(np.abs(np.diff(out.astype(np.float32))))) < 3000
    # Meme taux : aucun traitement.
    assert StreamResampler(16000, 16000).process(source[:10]).size == 10


def test_mic_recorder_writes_pcm_sink(tmp_path, monkeypatch) -> None:
    from vox import recorder

    callbacks: dict = {}

    class _FakeStream:
        def __init__(self, **kwargs) -> None:
            callbacks["callback"] = kwargs["callback"]

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(recorder.sd, "InputStream", lambda **kwargs: _FakeStream(**kwargs))
    sink = tmp_path / "micro.pcm"
    device = recorder.Recorder(samplerate=16000, sink=sink)
    device.start()

    chunk = np.full((1600, 1), 5000, dtype=np.int16)
    callbacks["callback"](chunk, 1600, None, None)
    device.flush()

    assert sink.exists() and sink.stat().st_size == 3200
    assert device.written_seconds == pytest.approx(0.1)
    assert device.has_speech
    assert device.stop() == b""  # avec un sink, le WAV se finalise a cote
    assert not device.recording
