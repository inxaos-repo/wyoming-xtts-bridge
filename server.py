#!/usr/bin/env python3
"""
Wyoming protocol bridge for XTTS v2 TTS server.

Wraps an existing XTTS server (e.g., http://192.168.2.25:8020)
as a Wyoming TTS provider that Home Assistant can discover and use.

Supports voice cloning via a reference WAV file.
Supports streaming for low-latency first-byte audio.

Usage:
    python server.py --xtts-url http://192.168.2.25:8020 \
                     --voice-wav /data/reference.wav \
                     --uri tcp://0.0.0.0:10400
"""

import argparse
import asyncio
import io
import logging
import struct
import wave
from functools import partial

import aiohttp
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.tts import Synthesize
from wyoming.server import AsyncEventHandler, AsyncServer

_LOGGER = logging.getLogger(__name__)

# WAV header is 44 bytes
WAV_HEADER_SIZE = 44


class XttsBridgeHandler(AsyncEventHandler):
    """Handle Wyoming TTS events by forwarding text to XTTS v2."""

    def __init__(
        self,
        wyoming_info: Info,
        xtts_url: str,
        voice_wav: str,
        language: str,
        streaming: bool,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.wyoming_info = wyoming_info
        self.xtts_url = xtts_url
        self.voice_wav = voice_wav
        self.language = language
        self.streaming = streaming

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info.event())
            return True

        if Synthesize.is_type(event.type):
            synthesize = Synthesize.from_event(event)
            text = synthesize.text
            voice_name = synthesize.voice.name if synthesize.voice else "bob"

            _LOGGER.info("Synthesizing: '%s' (voice: %s, streaming: %s)", text[:80], voice_name, self.streaming)

            if self.streaming:
                success = await self._synthesize_streaming(text)
            else:
                audio_bytes = await self._synthesize_full(text)
                success = await self._send_audio(audio_bytes) if audio_bytes else False

            if not success:
                _LOGGER.error("No audio returned from XTTS")
                await self.write_event(
                    AudioStart(rate=24000, width=2, channels=1).event()
                )
                await self.write_event(AudioStop().event())

            return True

        return True

    async def _synthesize_streaming(self, text: str) -> bool:
        """Stream audio from XTTS /tts/stream endpoint, sending Wyoming audio
        events as chunks arrive. This gives much lower time-to-first-byte."""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "text": text,
                    "language": self.language,
                    "speaker_wav": self.voice_wav,
                }

                async with session.post(
                    f"{self.xtts_url}/tts/stream",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120, sock_read=60),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        _LOGGER.error("XTTS stream error %d: %s", resp.status, body[:200])
                        return False

                    # Read the streaming response
                    header_parsed = False
                    rate = 24000
                    width = 2
                    channels = 1
                    buffer = bytearray()
                    audio_started = False
                    total_bytes = 0

                    async for chunk in resp.content.iter_any():
                        buffer.extend(chunk)

                        if not header_parsed and len(buffer) >= WAV_HEADER_SIZE:
                            # Parse WAV header to get format
                            try:
                                # RIFF header: 4s I 4s  fmt: 4s I H H I I H H  data: 4s I
                                riff, file_size, wave_tag = struct.unpack_from('<4sI4s', buffer, 0)
                                fmt_tag, fmt_size, audio_fmt, ch, sr, byte_rate, block_align, bps = \
                                    struct.unpack_from('<4sIHHIIHH', buffer, 12)
                                rate = sr
                                width = bps // 8
                                channels = ch
                                _LOGGER.debug("Stream format: %dHz %dbit %dch", rate, width * 8, channels)
                            except Exception:
                                _LOGGER.warning("Failed to parse WAV header, using defaults")

                            header_parsed = True

                            # Send Wyoming AudioStart
                            await self.write_event(
                                AudioStart(rate=rate, width=width, channels=channels).event()
                            )
                            audio_started = True

                            # Send any audio data after the header
                            pcm_data = bytes(buffer[WAV_HEADER_SIZE:])
                            buffer.clear()

                            if pcm_data:
                                await self.write_event(
                                    AudioChunk(
                                        audio=pcm_data,
                                        rate=rate, width=width, channels=channels,
                                    ).event()
                                )
                                total_bytes += len(pcm_data)
                        elif header_parsed:
                            # Send PCM data directly
                            pcm_data = bytes(buffer)
                            buffer.clear()

                            if pcm_data:
                                await self.write_event(
                                    AudioChunk(
                                        audio=pcm_data,
                                        rate=rate, width=width, channels=channels,
                                    ).event()
                                )
                                total_bytes += len(pcm_data)

                    if audio_started:
                        await self.write_event(AudioStop().event())
                        _LOGGER.info("Streamed %d bytes of audio", total_bytes)
                        return True
                    else:
                        _LOGGER.error("No audio data received in stream")
                        return False

        except Exception:
            _LOGGER.exception("Failed to stream audio from XTTS")
            return False

    async def _synthesize_full(self, text: str) -> bytes | None:
        """Send text to XTTS /tts endpoint and return full WAV audio bytes."""
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
                        _LOGGER.error("XTTS API error %d: %s", resp.status, body[:200])
                        return None
        except Exception:
            _LOGGER.exception("Failed to synthesize speech")
            return None

    async def _send_audio(self, audio_bytes: bytes) -> bool:
        """Parse full WAV and send as Wyoming audio events."""
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

            # Send in 1-second chunks
            chunk_size = rate * width * channels
            for i in range(0, len(frames), chunk_size):
                await self.write_event(
                    AudioChunk(
                        audio=frames[i: i + chunk_size],
                        rate=rate, width=width, channels=channels,
                    ).event()
                )

            await self.write_event(AudioStop().event())
            _LOGGER.debug("Sent %d bytes of audio", len(frames))
            return True

        except Exception:
            _LOGGER.exception("Failed to parse/send audio")
            return False


async def main() -> None:
    parser = argparse.ArgumentParser(description="Wyoming bridge for XTTS v2 TTS")
    parser.add_argument("--xtts-url", required=True, help="XTTS v2 server URL")
    parser.add_argument("--voice-wav", required=True, help="Path to voice reference WAV")
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    parser.add_argument("--uri", default="tcp://0.0.0.0:10400", help="Wyoming server bind address")
    parser.add_argument("--no-streaming", action="store_true", help="Disable streaming (use full synthesis)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    streaming = not args.no_streaming

    _attr = Attribution(name="Coqui", url="https://github.com/coqui-ai/TTS")
    voices = [TtsVoice(name="bob", description="Bob the Skull", languages=["en"], attribution=_attr, installed=True, version="2.0")]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{args.xtts_url}/voices", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    voice_data = await resp.json()
                    voice_list = voice_data.get("voices", []) if isinstance(voice_data, dict) else voice_data
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
                version="1.1.0",
                attribution=Attribution(name="Coqui", url="https://github.com/coqui-ai/TTS"),
                voices=voices,
            )
        ]
    )

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("Starting Wyoming XTTS bridge on %s → %s (streaming: %s)", args.uri, args.xtts_url, streaming)
    _LOGGER.info("Voice reference: %s", args.voice_wav)

    await server.run(
        partial(
            XttsBridgeHandler,
            wyoming_info,
            args.xtts_url,
            args.voice_wav,
            args.language,
            streaming,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
