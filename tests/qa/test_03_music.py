#!/usr/bin/env python3
"""QA Test 03: Music generation — generate background music tracks and inspect audio output.

Validates MusicGen / music_selector tools.
Outputs are saved to tests/qa/output/music_calm.mp3 for consumption by test_04_audio_mix.py.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lib.env_loader import load_env
load_env()

from tools.audio.music_gen import MusicGen
from tools.audio.music_selector import MusicSelector

OUT = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUT, exist_ok=True)

MUSIC_OUT = os.path.join(OUT, "music_calm.mp3")

print("=== QA Test 03: Music Generation ===")

music_tool = MusicGen()
status = music_tool.get_status()
print(f"MusicGen status: {status}")

if status.value == "available":
    result = music_tool.execute({
        "prompt": "calm ambient acoustic track, warm piano and subtle rhythm for documentary narration",
        "duration_seconds": 15,
        "output_path": MUSIC_OUT,
    })
    print(f"Execution success: {result.success}")
    if result.success:
        print(f"Generated music track: {MUSIC_OUT} ({os.path.getsize(MUSIC_OUT)} bytes)")
    else:
        print(f"Generation error: {result.error}")
else:
    print("MusicGen is unavailable (ELEVENLABS_API_KEY missing or invalid).")

# Also test music selector routing
selector = MusicSelector()
print(f"MusicSelector status: {selector.get_status()}")
sel_out = os.path.join(OUT, "music_selector_sample.mp3")
sel_res = selector.execute({
    "prompt": "upbeat corporate tech background",
    "duration_seconds": 10,
    "output_path": sel_out,
})
print(f"MusicSelector success: {sel_res.success}")
if sel_res.success:
    print(f"Selector routed music output: {sel_out}")
else:
    print(f"Selector status note: {sel_res.error}")
