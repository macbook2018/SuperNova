from __future__ import annotations

import wave
from io import BytesIO

import pysilk


class InvalidSilkAudioError(ValueError):
    """Raised when an uploaded payload is not a valid SILK V3 audio stream."""


class DecodedAudioTooLargeError(ValueError):
    """Raised when decoded PCM exceeds the configured in-memory limit."""


class _LimitedBytesIO(BytesIO):
    def __init__(self, max_bytes: int) -> None:
        super().__init__()
        self._max_bytes = max_bytes

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self._max_bytes:
            raise DecodedAudioTooLargeError(
                "Decoded SILK audio exceeds the configured PCM size limit"
            )
        return super().write(data)


def decode_silk_to_wav(
    silk_bytes: bytes,
    *,
    sample_rate: int = 24000,
    max_pcm_bytes: int = 64 * 1024 * 1024,
) -> bytes:
    """Decode a WeChat SILK V3 stream into mono, 16-bit PCM WAV bytes.

    Both the standard SILK V3 header and WeChat's 0x02-prefixed header are
    accepted. Decoding and WAV packaging happen in memory; no temporary files
    are created.
    """
    if not silk_bytes or not silk_bytes.startswith((b"#!SILK_V3", b"\x02#!SILK_V3")):
        raise InvalidSilkAudioError("Missing or unsupported SILK V3 header")
    if sample_rate not in {8000, 12000, 16000, 24000, 48000}:
        raise ValueError("Unsupported SILK output sample rate")
    if max_pcm_bytes < 2:
        raise ValueError("PCM output limit must be at least 2 bytes")

    pcm_output = _LimitedBytesIO(max_pcm_bytes)
    try:
        pysilk.decode(BytesIO(silk_bytes), pcm_output, sample_rate)
    except DecodedAudioTooLargeError:
        raise
    except MemoryError:
        raise
    except Exception as exc:
        raise InvalidSilkAudioError("SILK V3 decoding failed") from exc

    pcm_bytes = pcm_output.getvalue()
    if not pcm_bytes or len(pcm_bytes) % 2:
        raise InvalidSilkAudioError("SILK V3 stream produced invalid PCM data")

    wav_output = BytesIO()
    try:
        with wave.open(wav_output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)
    except (wave.Error, OSError) as exc:
        raise InvalidSilkAudioError("Unable to package decoded PCM as WAV") from exc

    return wav_output.getvalue()
