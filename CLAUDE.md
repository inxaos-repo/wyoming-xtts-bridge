# CLAUDE.md — wyoming-xtts-bridge

## What This Is
A Wyoming protocol bridge that wraps an existing XTTS v2 server for Home Assistant voice pipelines, with voice cloning support.

## Architecture
- Single Python file (server.py) — no framework, minimal dependencies
- Receives text via Wyoming Synthesize events, forwards to XTTS HTTP API, returns audio chunks
- Voice cloning via reference WAV file (6-15 seconds of clean speech)
- Runs as a Docker container on the same host as XTTS for minimal latency

## Key Files
- `server.py` — The entire bridge (XttsBridgeHandler class)
- `Dockerfile` — python:3.12-slim based, GHCR-labeled
- `.github/workflows/build.yml` — Builds + pushes to GHCR on push to main or tag

## Dependencies
- `wyoming>=1.5.0` — Wyoming protocol library (AsyncServer, event types)
- `aiohttp>=3.9.0` — HTTP client for XTTS API

## Production Deployment
- Runs on Ix (192.168.2.25) with `--network host`
- Port 10400 (Wyoming TTS)
- Upstream: XTTS v2 at 127.0.0.1:8020
- Voice reference WAV mounted at /data/bob-reference.wav
- Managed by Ansible role `inference-stack` in homelab-infra repo

## Pitfalls
- Wyoming Info classes (TtsVoice, TtsProgram) require `version`, `attribution`, and `installed` params
- XTTS returns WAV audio — must parse headers and send as AudioStart + AudioChunk + AudioStop events
- Send audio in 1-second chunks to avoid blocking the event loop
- Voice WAV path is the path ON THE XTTS SERVER (or mounted volume), not the bridge container
- XTTS can take up to 10s for long sentences — set appropriate timeouts

