"""Tests du routeur automatique (aucune dependance audio)."""

from __future__ import annotations

from vox.audiofiles import AudioInfo
from vox.routing import CALL_MAX_SPEAKERS, DEFAULT_MAX_SPEAKERS, analyse

MONO = AudioInfo(duration=60.0, channels=1, samplerate=48000, codec="aac")
STEREO = AudioInfo(duration=60.0, channels=2, samplerate=48000, codec="aac")


def test_mono_is_diarized() -> None:
    strategy = analyse(MONO)
    assert strategy.kind == "mono"
    assert len(strategy.tracks) == 1
    track = strategy.tracks[0]
    assert track.channel is None
    assert track.diarize
    assert track.max_speakers == DEFAULT_MAX_SPEAKERS


def test_call_tightens_speaker_cap() -> None:
    strategy = analyse(MONO, is_call=True)
    assert strategy.tracks[0].max_speakers == CALL_MAX_SPEAKERS


def test_duplicated_stereo_is_treated_as_mono() -> None:
    strategy = analyse(STEREO, correlation=0.999)
    assert strategy.kind == "mono"
    assert strategy.tracks[0].diarize


def test_unknown_correlation_falls_back_to_mono() -> None:
    strategy = analyse(STEREO, correlation=None)
    assert strategy.kind == "mono"


def test_distinct_channels_give_two_tracks() -> None:
    strategy = analyse(STEREO, correlation=0.2, co_activity=0.2)
    assert strategy.kind == "canaux"
    assert [track.channel for track in strategy.tracks] == [0, 1]
    assert all(not track.diarize for track in strategy.tracks)
    assert [track.label for track in strategy.tracks] == ["Canal gauche", "Canal droit"]


def test_same_audio_on_both_channels_is_mono() -> None:
    # Cas reel : un appel mono mixe en stereo. Correlation moderee (0,8) mais
    # les deux canaux parlent en meme temps : c'est le meme son.
    strategy = analyse(STEREO, correlation=0.8, co_activity=0.95)
    assert strategy.kind == "mono"
    assert "Même son" in strategy.reason


def test_two_tracks_need_both_criteria() -> None:
    # Canaux qui se ressemblent mais ne parlent pas ensemble : deux voix.
    strategy = analyse(STEREO, correlation=0.8, co_activity=0.2)
    assert strategy.kind == "canaux"


def test_custom_track_labels() -> None:
    strategy = analyse(STEREO, correlation=0.1, labels=("Moi", "Interlocuteur"))
    assert [track.label for track in strategy.tracks] == ["Moi", "Interlocuteur"]


def test_explicit_speaker_cap_wins() -> None:
    assert analyse(MONO, max_speakers=4).tracks[0].max_speakers == 4
    assert analyse(MONO, is_call=True, max_speakers=5).tracks[0].max_speakers == 5
    # Pistes separees par canal : aucune diarisation, donc pas de plafond.
    assert analyse(STEREO, correlation=0.1).tracks[0].max_speakers is None
