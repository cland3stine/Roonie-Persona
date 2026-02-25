"""Audio capture from a system audio device via sounddevice.

Provides a threaded ring buffer that continuously reads audio and exposes
the latest chunk for downstream consumers (transcription, wake-word, etc.).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class AudioCapture:
    """Threaded ring buffer that reads from a Windows audio input device.

    Parameters
    ----------
    device : str or int or None
        Device name (e.g. "Broadcast Stream Mix") or PortAudio device index.
        ``None`` selects the system default input device.
    sample_rate : int
        Sample rate in Hz. Whisper expects 16 000.
    channels : int
        Number of audio channels. Mono (1) is standard for speech.
    chunk_seconds : float
        Duration of each audio chunk returned by :meth:`get_chunk`.
    """

    def __init__(
        self,
        *,
        device: str | int | None = None,
        sample_rate: int = 16_000,
        channels: int = 1,
        chunk_seconds: float = 3.0,
    ) -> None:
        self._device = device
        self._sample_rate = int(sample_rate)
        self._channels = int(channels)
        self._chunk_seconds = float(chunk_seconds)

        self._lock = threading.Lock()
        self._buffer: list[np.ndarray] = []
        self._last_rms: float = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stream: object | None = None  # sounddevice.InputStream

    # ── public API ──────────────────────────────────────────────

    def start(self) -> None:
        """Start the capture thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="roonie-audio-capture", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the capture thread to stop."""
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_chunk(self) -> Optional[np.ndarray]:
        """Return accumulated audio as a single 1-D float32 array and clear the buffer.

        Returns ``None`` if no audio has been captured since the last call.
        """
        with self._lock:
            if not self._buffer:
                return None
            chunk = np.concatenate(self._buffer, axis=0).astype(np.float32)
            self._buffer.clear()
        return chunk

    # ── internals ───────────────────────────────────────────────

    def get_level(self) -> float:
        """Return current audio RMS level (0.0-1.0 range)."""
        return self._last_rms

    def _callback(self, indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
        if status:
            logger.debug("sounddevice status: %s", status)
        samples = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        self._last_rms = float(np.sqrt(np.mean(samples ** 2)))
        with self._lock:
            self._buffer.append(samples)

    def _run(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice not installed — audio capture disabled")
            return

        try:
            self._stream = sd.InputStream(
                device=self._device,
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()  # type: ignore[union-attr]
            logger.info(
                "AudioCapture started (device=%s, rate=%d, channels=%d)",
                self._device, self._sample_rate, self._channels,
            )
            while not self._stop.is_set():
                self._stop.wait(0.25)
        except Exception:
            logger.exception("AudioCapture failed")
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop()  # type: ignore[union-attr]
                    self._stream.close()  # type: ignore[union-attr]
                except Exception:
                    pass
            logger.info("AudioCapture stopped")
