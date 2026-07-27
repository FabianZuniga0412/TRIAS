from pathlib import Path

import audio_utils


def test_non_wav_is_converted_to_mono_16khz(monkeypatch, tmp_path):
    source = tmp_path / "input.ogg"
    source.write_bytes(b"source")
    destination = tmp_path / "converted.wav"
    captured = []

    monkeypatch.setattr(audio_utils, "new_output_path", lambda suffix: destination)
    monkeypatch.setattr(audio_utils, "_run_ffmpeg", lambda arguments: captured.extend(arguments))

    assert audio_utils.convert_to_wav(source) == destination
    assert captured == ["-i", str(source), "-ac", "1", "-ar", "16000", str(destination)]


def test_wav_is_not_reconverted(tmp_path):
    source = tmp_path / "input.wav"
    source.write_bytes(b"wav")
    assert audio_utils.convert_to_wav(source) == source


def test_ogg_conversion_uses_opus(monkeypatch, tmp_path):
    source = tmp_path / "reply.wav"
    source.write_bytes(b"wav")
    destination = tmp_path / "reply.ogg"
    captured = []

    monkeypatch.setattr(audio_utils, "new_output_path", lambda suffix: destination)
    monkeypatch.setattr(audio_utils, "_run_ffmpeg", lambda arguments: captured.extend(arguments))

    assert audio_utils.convert_wav_to_ogg(source) == destination
    assert "libopus" in captured
    assert "voip" in captured
