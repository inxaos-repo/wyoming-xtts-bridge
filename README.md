# Wyoming XTTS Bridge

A [Wyoming protocol](https://github.com/rhasspy/wyoming) bridge that wraps an existing [XTTS v2](https://github.com/coqui-ai/TTS) server as a Text-to-Speech provider for Home Assistant voice pipelines — with voice cloning support.

## Why?

If you already have XTTS running on a GPU box, this bridge exposes it to Home Assistant via the Wyoming protocol. Point it at a voice reference WAV and get custom voice cloning in your voice pipeline.

## Quick Start

### Docker (recommended)

```bash
docker run -d --name wyoming-xtts-bridge \
  --restart unless-stopped \
  -p 10400:10400 \
  -v /path/to/voices:/data:ro \
  ghcr.io/inxaos-repo/wyoming-xtts-bridge:main \
  --xtts-url http://YOUR_XTTS_HOST:8020 \
  --voice-wav /data/reference.wav \
  --uri tcp://0.0.0.0:10400
```

### Python

```bash
pip install -r requirements.txt
python server.py --xtts-url http://localhost:8020 --voice-wav /data/reference.wav --uri tcp://0.0.0.0:10400
```

## Voice Cloning

Place a 6-15 second WAV file of clean speech in your data directory and reference it with `--voice-wav`. XTTS will clone the voice for all TTS output. For best results:

- Use clear, clean speech with minimal background noise
- 6-15 seconds of consistent voice
- Single speaker only

## Home Assistant Setup

1. **Settings → Integrations → Add Integration → Wyoming Protocol**
2. Host: `<bridge-host>`, Port: `10400`
3. Create/edit a Voice Pipeline and select the new TTS engine

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--xtts-url` | required | URL of XTTS v2 server |
| `--voice-wav` | required | Path to voice reference WAV (on XTTS server or mounted) |
| `--language` | `en` | Language code |
| `--uri` | `tcp://0.0.0.0:10400` | Wyoming server bind address |
| `--debug` | off | Enable debug logging |

## License

MIT
