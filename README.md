# Live Audio Translator

Translates whatever your PC is playing (videos, streams, games, calls), live, from one language into another. Results show up as an always-on-top subtitle overlay and in a scrolling transcript.

**How it works:** WASAPI loopback capture → speech recognition with Whisper (faster-whisper, runs locally on your CPU) → a translator of your choice → subtitles.

## Start it

Double-click **`Live Audio Translator.bat`**. The first launch sets up Python packages if they're missing. The first **Start** downloads the speech model you picked (`small` is ~480 MB) into `models\`.

1. **Audio source:** "Default output" captures what you hear. If you have a GoXLR, pick the channel the audio actually plays on (e.g. `[PC audio] Music` or `Game`). `[Mic]` entries translate a microphone instead.
2. **From / To:** pick the languages. "Auto-detect" works too. Once the last three lines agree on a language, the app reuses it to save ~1 s per line.
3. **Translator:**
   | Translator | Needs |
   |---|---|
   | Google Translate (built-in, free) | nothing, works out of the box |
   | DeepL API | API key (free keys end in `:fx`, the right endpoint is picked automatically) |
   | Google Cloud Translation API | API key |
   | Microsoft Azure Translator | key + region |
   | LibreTranslate | server URL (+ optional key) |
   | Whisper offline | nothing, no internet, but **only translates into English** |

   Click **API settings…** to enter keys, then **Test** to check them. Keys are saved locally in `config.json` (plain text, never uploaded anywhere except to that provider).
4. Click **Start translating**.

## Overlay

- Drag to move · mouse wheel = text size · Ctrl + wheel = width · right-click = hide
- **Click-through** lets mouse clicks pass through it (handy over games).
- It stays on top of borderless/windowed games. True exclusive-fullscreen games draw over it, so switch those to borderless.

## Speech models

| Model | Notes |
|---|---|
| tiny / base | fastest, more mistakes |
| **small** | default; ~1 s per line on a Ryzen 9 7900X |
| medium | noticeably more accurate, ~2–3× slower |
| large-v3-turbo / large-v3 | best accuracy, can lag behind on CPU |

There's no GPU acceleration here: faster-whisper's GPU support is NVIDIA/CUDA only, so on an AMD card it runs on the CPU.

**Live partial text** shows a rough translation while someone is still talking, then replaces it with the final line. It sends more requests to paid APIs. Untick it to save DeepL/Azure quota.

## Tuning (config.json)

- `silence_s` (0.6): pause length that ends a line. Lower = snappier, but more fragmented.
- `max_segment_s` (7): longest line before it's cut (for nonstop talkers or music).
- `sensitivity` (1.0): raise it (e.g. 1.5) if quiet speech is missed, lower it if background noise triggers lines.

## Adding another translator

Subclass `Translator` in `lat/translators.py`, implement `translate(text, source, target)`, list any settings in `fields`, and add it to `PROVIDERS`. It then appears in the dropdown and in API settings automatically.

## Test

```bash
.venv\Scripts\python tests\test_pipeline.py tests\sample_en.wav Spanish
```

This feeds a WAV file through the real pipeline without playing any sound.
