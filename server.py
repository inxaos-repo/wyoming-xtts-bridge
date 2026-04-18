#!/usr/bin/env python3
"""
Wyoming protocol bridge for XTTS v2 TTS server.

Wraps an existing XTTS server (e.g., http://192.168.2.25:8020)
as a Wyoming TTS provider that Home Assistant can discover and use.

Supports voice cloning via a reference WAV file.

Usage:
    python server.py --xtts-url http://192.168.2.25:8020 \
                     --voice-wav /data/bob-reference.wav \
                     --uri tcp://0.0.0.0:10400
"""

import argparse
import asyncio
import io
import logging
import wave
from functools import partial
from pathlib import Path

import aiohttp
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.tts import Synthesize
from wyoming.server import AsyncEventHandler, AsyncServer

_LOGGER = logging.getLogger(__name__)


class XttsBridgeHandler(AsyncEventHandler):
    """Handle Wyoming TTS events by forwarding text to XTTS v2."""

    def __init__(
        self,
        wyoming_info: Info,
        xtts_url: str,
        voice_wav: str,
        language: str,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.wyoming_info = wyoming_info
        self.xtts_url = xtts_url
        self.voice_wav = voice_wav
        self.language = language

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info.event())
            return True

        if Synthesize.is_type(event.type):
            synthesize = Synthesize.from_event(event)
            text = synthesize.text
            voice_name = synthesize.voice.name if synthesize.voice else "bob"

            _LOGGER.info("Synthesizing: '%s' (voice: %s)", text[:80], voice_name)
            audio_bytes = await self._synthesize(text)

            if audio_bytes:
                await self._send_audio(audio_bytes)
            else:
                _LOGGER.error("No audio returned from XTTS")
                # Send silence so the pipeline doesn't hang
                await self.write_event(
                    AudioStart(rate=22050, width=2, channels=1).event()
                )
                await self.write_event(AudioStop().event())

            return True

        return True

    async def _synthesize(self, text: str) -> bytes | None:
        """Send text to XTTS and return WAV audio bytes."""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "text": text,
                    "language": self.language,
                    "speaker_wav": self.voice_wav,
                }

                async with session.post(
                    f"{self.xtts_url}/tts",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    else:
                        body = await resp.text()
                        _LOGGER.error("XTTS API error %d: %s", resp.status, body)
                        return None
        except Exception:
            _LOGGER.exception("Failed to synthesize speech")
            return None

    async def _send_audio(self, audio_bytes: bytes) -> None:
        """Parse WAV and send as Wyoming audio events."""
        try:
            with io.BytesIO(audio_bytes) as wav_io:
                with wave.open(wav_io, "rb") as wav:
                    rate = wav.getframerate()
                    width = wav.getsampwidth()
                    channels = wav.getnchannels()
                    frames = wav.readframes(wav.getnframes())

            await self.write_event(
                AudioStart(rate=rate, width=width, channels=channels).event()
            )

            # Send in 1-second chunks to avoid blocking
            chunk_size = rate * width * channels
            for i in range(0, len(frames), chunk_size):
                await self.write_event(
                    AudioChunk(
                        audio=frames[i : i + chunk_size],
                        rate=rate,
                        width=width,
                        channels=channels,
                    ).event()
                )

            await self.write_event(AudioStop().event())
            _LOGGER.debug("Sent %d bytes of audio (%d chunks)", len(frames), len(frames) // chunk_size + 1)

        except Exception:
            _LOGGER.exception("Failed to parse/send audio")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Wyoming bridge for XTTS v2 TTS")
    parser.add_argument("--xtts-url", required=True, help="XTTS v2 server URL")
    parser.add_argument("--voice-wav", required=True, help="Path to voice reference WAV (on the XTTS server)")
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    parser.add_argument("--uri", default="tcp://0.0.0.0:10400", help="Wyoming server URI")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    # Discover available voices from XTTS server
    _attr = Attribution(name="Coqui", url="https://github.com/coqui-ai/TTS")
    voices = [TtsVoice(name="bob", description="Bob the Skull", languages=["en"], attribution=_attr, installed=True, version="2.0")]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{args.xtts_url}/voices", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    voice_list = await resp.json()
                    if isinstance(voice_list, list):
                        for v in voice_list:
                            name = v if isinstance(v, str) else v.get("name", "unknown")
                            if name != "bob":
                                voices.append(TtsVoice(name=name, languages=["en"], attribution=_attr, installed=True, version="2.0"))
                        _LOGGER.info("Discovered %d voices from XTTS", len(voices))
    except Exception:
        _LOGGER.warning("Could not discover XTTS voices, using default 'bob'")

    wyoming_info = Info(
        tts=[
            TtsProgram(
                name="xtts-bridge",
                description="Bridge to XTTS v2 TTS server with voice cloning",
                installed=True,
                version="1.0.0",
                attribution=Attribution(name="Coqui", url="https://github.com/coqui-ai/TTS"),
                voices=voices,
            )
        ]
    )

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("Starting Wyoming XTTS bridge on %s → %s", args.uri, args.xtts_url)
    _LOGGER.info("Voice reference: %s", args.voice_wav)

    await server.run(
        partial(
            XttsBridgeHandler,
            wyoming_info,
            args.xtts_url,
            args.voice_wav,
            args.language,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
