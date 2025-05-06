#!/usr/bin/env python3
"""
Transcribe a single 30‑second WAV chunk with Wyoming Faster‑Whisper
and write it to an .srt subtitle file.

Example:
    python wyoming_chunk2srt.py --wav chunk_000.wav --offset 0 \
        --host 10.0.10.23 --port 10300 --model large-v3-turbo
"""

import argparse, asyncio, wave, textwrap
from datetime import timedelta
from pathlib import Path

from wyoming.client import AsyncTcpClient                       # :contentReference[oaicite:2]{index=2}
from wyoming.asr import Transcribe, Transcript                  # :contentReference[oaicite:3]{index=3}
from wyoming.audio import AudioStart, AudioStop, wav_to_chunks  # :contentReference[oaicite:4]{index=4}
from wyoming.event import async_write_event, async_read_event
from wyoming.error import TransportClosed

## ---------- helpers ---------------------------------------------------------

def hhmmss_ms(sec: float) -> str:
    td = timedelta(seconds=sec)
    total = int(td.total_seconds())
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    ms = int((td.total_seconds() - total) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def make_srt_block(index: int, text: str, start: float, dur: float) -> str:
    end = start + dur
    lines = textwrap.fill(text, width=42)
    return f"{index}\n{hhmmss_ms(start)} --> {hhmmss_ms(end)}\n{lines}\n\n"

## ---------- core logic ------------------------------------------------------

async def transcribe_chunk(path: Path, host: str, port: int,
                           model: str, lang: str = "en") -> str:
    """Return full transcript text of one WAV file."""
    client = AsyncTcpClient(host, port)                         # :contentReference[oaicite:5]{index=5}
    await client.connect()

    # Ask the server to use the chosen model/language               :contentReference[oaicite:6]{index=6}
    await async_write_event(Transcribe(name=model, language=lang).event(),
                            client.writer)

    with wave.open(str(path), "rb") as wav:
        # Start stream                                              :contentReference[oaicite:7]{index=7}
        await async_write_event(
            AudioStart(rate=wav.getframerate(),
                       width=wav.getsampwidth(),
                       channels=wav.getnchannels()).event(),
            client.writer)

        # Stream audio in small PCM chunks                          :contentReference[oaicite:8]{index=8}
        for chunk in wav_to_chunks(wav, samples_per_chunk=1024):
            await async_write_event(chunk.event(), client.writer)

        # Tell the server we’re done
        await async_write_event(AudioStop().event(), client.writer)

    # Collect transcript events (one per segment)                   :contentReference[oaicite:9]{index=9}
    parts: list[str] = []
    try:
        while True:
            event = await asyncio.wait_for(async_read_event(client.reader),
                                           timeout=90)
            if Transcript.is_type(event.type):
                parts.append(Transcript.from_event(event).text)
            else:
                break
    except (asyncio.TimeoutError, TransportClosed):
        pass

    await client.disconnect()
    return " ".join(parts).strip()

async def main(args):
    text = await transcribe_chunk(Path(args.wav),
                                  host=args.host,
                                  port=args.port,
                                  model=args.model,
                                  lang=args.lang)

    srt_txt = make_srt_block(1, text, args.offset, args.duration)
    out_path = Path(args.wav).with_suffix(".srt")
    out_path.write_text(srt_txt, encoding="utf-8")
    print(f"Wrote {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="30‑s WAV → SRT via Wyoming")
    parser.add_argument("--wav", required=True, help="Path to 30‑s WAV file")
    parser.add_argument("--host", default="10.0.10.23")
    parser.add_argument("--port", type=int, default=10300)
    parser.add_argument("--model", default="large-v3-turbo",
                        help="Model name inside the container")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--offset", type=float, default=0.0,
                        help="Start time (s) of this chunk in the episode")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="Duration (s) – keep at 30 unless you changed chunk size")
    asyncio.run(main(parser.parse_args()))
