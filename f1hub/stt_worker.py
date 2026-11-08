"""Speech-to-text worker for team radio. Runs in the project's .venv (it needs faster-whisper),
so the main app keeps working with plain Python. Protocol: one JSON object per line.

  stdin:  {"id": "...", "path": "/abs/clip.mp3"}
  stdout: {"id": "...", "text": "...", "segments": [[start, end, text], ...]}  or  {"id": ..., "error": "..."}
The first line written is {"ready": true, "model": "..."} once the model has loaded.
"""
import json
import os
import sys

# Whisper's prompt nudges it towards the words it will hear: names, team-radio jargon.
F1_PROMPT = ("Formula 1 team radio. Box, box. Copy. Stay out. Push now. Softs, mediums, hards, inters. "
             "Safety car, VSC, red flag, DRS, overtake mode, deg, delta, gap, P1, undercut, plan B, "
             "Max, Lando, Oscar, Charles, Lewis, George, Kimi, Fernando, Yuki, Isack, Carlos, Alex.")


def load_audio(path, rate=16000):
    """Decode with the system ffmpeg to 16 kHz mono float32 (faster-whisper's own decoder breaks on newer PyAV)."""
    import subprocess
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-f", "s16le", "-ac", "1",
                          "-ar", str(rate), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def main():
    from faster_whisper import WhisperModel
    name = os.environ.get("F1HUB_STT_MODEL", "small.en")
    model = WhisperModel(name, device="cpu", compute_type="int8",
                         cpu_threads=int(os.environ.get("F1HUB_STT_THREADS", "4")))
    print(json.dumps({"ready": True, "model": name}), flush=True)
    for line in sys.stdin:
        try:
            job = json.loads(line)
        except ValueError:
            continue
        try:
            segs, _info = model.transcribe(load_audio(job["path"]), language="en", beam_size=5, vad_filter=True,
                                           initial_prompt=F1_PROMPT, condition_on_previous_text=False)
            segs = [[round(s.start, 2), round(s.end, 2), s.text.strip()] for s in segs]
            out = {"id": job["id"], "text": " ".join(s[2] for s in segs).strip(), "segments": segs}
        except Exception as e:      # a bad clip must not take the worker down
            out = {"id": job.get("id"), "error": f"{type(e).__name__}: {e}"}
        print(json.dumps(out), flush=True)


if __name__ == "__main__":
    main()
