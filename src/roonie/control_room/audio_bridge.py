"""AudioInputBridge — pipes audio capture through Whisper + wake-word detection
into the existing Roonie payload pipeline.

Architecture: delegation pattern. AudioInputBridge receives the ``LiveChatBridge``
instance and delegates to its ``_emit_payload_message()`` method so that voice
events flow through the exact same pipeline as Twitch chat messages.  No changes
to ProviderDirector, context buffer, or output gate are required.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_audio_config(data_dir: Path) -> Dict[str, Any]:
    """Read audio_config.json, returning defaults for any missing keys."""
    defaults: Dict[str, Any] = {
        "enabled": False,
        "device_name": "",
        "sample_rate": 16_000,
        "whisper_model": "base.en",
        "whisper_device": "cuda",
        "wake_word_enabled": True,
        "transcription_interval_seconds": 3.0,
        "voice_default_user": "Art",
    }
    path = data_dir / "audio_config.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            defaults.update(raw)
    except (OSError, json.JSONDecodeError):
        pass
    return defaults


class AudioInputBridge:
    """Threaded bridge: audio capture → Whisper → wake word → payload pipeline.

    Parameters
    ----------
    live_bridge : LiveChatBridge
        Existing chat bridge whose ``_emit_payload_message`` we delegate to.
    storage : DashboardStorage
        Dashboard storage instance (used to read config & senses status).
    logger : callable, optional
        Logging function matching the control-room ``_append_log`` signature.
    """

    def __init__(
        self,
        *,
        live_bridge: Any,
        storage: Any,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._live_bridge = live_bridge
        self._storage = storage
        self._ext_logger = logger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── public API (matches LiveChatBridge/EventSubBridge) ──────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="roonie-audio-bridge", daemon=True,
        )
        self._thread.start()
        self._log("[AudioInputBridge] started")

    def stop(self) -> None:
        self._stop.set()
        self._log("[AudioInputBridge] stop requested")

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── logging ─────────────────────────────────────────────────

    def _log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        line = f"{_utc_now_iso()} {text}"
        if callable(self._ext_logger):
            try:
                self._ext_logger(line)
                return
            except Exception:
                pass
        print(line)

    # ── core loop ───────────────────────────────────────────────

    def _run(self) -> None:
        # Lazy imports so the bridge silently disables if deps are missing.
        try:
            from audio.capture import AudioCapture
            from audio.transcriber import WhisperTranscriber
            from audio.wake_word import WakeWordDetector
        except ImportError as exc:
            self._log(
                f"[AudioInputBridge] audio dependencies not available ({exc}); "
                "bridge will remain inactive. Install: pip install faster-whisper sounddevice numpy"
            )
            return

        data_dir = Path("data")
        if hasattr(self._storage, "data_dir"):
            data_dir = self._storage.data_dir

        config = _load_audio_config(data_dir)
        if not config.get("enabled", False):
            self._log("[AudioInputBridge] disabled by audio_config.json (enabled=false)")
            return

        device = config.get("device_name") or None
        if isinstance(device, str) and not device.strip():
            device = None
        sample_rate = int(config.get("sample_rate", 16_000))
        interval = float(config.get("transcription_interval_seconds", 3.0))
        default_user = str(config.get("voice_default_user", "Art")).strip() or "Art"
        wake_word_enabled = bool(config.get("wake_word_enabled", True))

        capture = AudioCapture(
            device=device,
            sample_rate=sample_rate,
            channels=1,
            chunk_seconds=interval,
        )
        transcriber = WhisperTranscriber(
            model_size=str(config.get("whisper_model", "base.en")),
            device=str(config.get("whisper_device", "cuda")),
            compute_type="float16" if config.get("whisper_device", "cuda") == "cuda" else "int8",
        )
        detector = WakeWordDetector()

        capture.start()
        self._log(
            f"[AudioInputBridge] capture started (device={device}, rate={sample_rate}, "
            f"interval={interval}s, wake_word={wake_word_enabled})"
        )

        try:
            while not self._stop.is_set():
                self._stop.wait(interval)
                if self._stop.is_set():
                    break

                chunk = capture.get_chunk()
                if chunk is None or len(chunk) == 0:
                    continue

                try:
                    text = transcriber.transcribe_text(chunk)
                except Exception as exc:
                    self._log(f"[AudioInputBridge] transcription error: {exc}")
                    continue

                if not text.strip():
                    continue

                self._log(f"[AudioInputBridge] transcribed: {text[:120]}")

                if not wake_word_enabled:
                    # Without wake-word gating, every transcription is emitted.
                    self._emit_voice_event(
                        user=default_user,
                        message=text,
                        raw_text=text,
                        confidence=1.0,
                    )
                    continue

                result = detector.detect(text)
                if not result.detected:
                    continue

                message = result.remaining_text or text
                self._log(
                    f"[AudioInputBridge] wake word detected "
                    f"(trigger={result.trigger_phrase!r}, confidence={result.confidence}, "
                    f"message={message[:80]!r})"
                )
                self._emit_voice_event(
                    user=default_user,
                    message=message,
                    raw_text=text,
                    confidence=result.confidence,
                )
        except Exception:
            logger.exception("[AudioInputBridge] unexpected error in run loop")
        finally:
            capture.stop()
            capture.join(timeout=2.0)
            self._log("[AudioInputBridge] stopped")

    # ── event emission ──────────────────────────────────────────

    def _emit_voice_event(
        self,
        *,
        user: str,
        message: str,
        raw_text: str,
        confidence: float,
    ) -> None:
        """Delegate a voice event to the LiveChatBridge payload pipeline."""
        metadata_extra: Dict[str, Any] = {
            "platform": "voice",
            "source": "voice",
            "is_direct_mention": True,
            "voice_confidence": confidence,
            "voice_raw_text": raw_text,
        }
        try:
            result = self._live_bridge._emit_payload_message(
                actor=user,
                message=message,
                channel="voice",
                is_direct_mention=True,
                metadata_extra=metadata_extra,
            )
            emitted = bool(result.get("emitted", False))
            reason = result.get("reason", "UNKNOWN")
            event_id = result.get("event_id", "?")
            self._log(
                f"[AudioInputBridge] event_id={event_id} emitted={emitted} reason={reason}"
            )
        except Exception as exc:
            self._log(f"[AudioInputBridge] emit error: {exc}")
