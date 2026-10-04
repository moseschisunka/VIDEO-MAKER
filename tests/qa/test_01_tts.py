#!/usr/bin/env python3
"""QA Test 01: TTS generation — generate spoken narration segments and inspect output.

Validates ElevenLabsTTS and tts_selector live generation.
Outputs are saved to tests/qa/output/tts_short.mp3 for consumption by test_04_audio_mix.py.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lib.env_loader import load_env
load_env()

from tools.audio.elevenlabs_tts import ElevenLabsTTS
from tools.audio.tts_selector import TTSSelector

OUT = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUT, exist_ok=True)

SPEECH_OUT = os.path.join(OUT, "tts_short.mp3")

print("=== QA Test 01: TTS Generation ===")

# Test 1: Direct ElevenLabsTTS Tool
print("\n--- Test 1.1: Direct ElevenLabsTTS Tool ---")
tts = ElevenLabsTTS()
status = tts.get_status()
print(f"ElevenLabsTTS status: {status}")

if status.value == "available":
    result = tts.execute({
        "text": "Welcome to OpenMontage. This is an automated quality assurance test for text to speech narration.",
        "voice": "George",
        "output_path": SPEECH_OUT,
    })
    print(f"Execution success: {result.success}")
    if result.success:
        print(f"Generated file: {SPEECH_OUT} ({os.path.getsize(SPEECH_OUT)} bytes)")
    else:
        print(f"Generation error: {result.error}")
else:
    print("ElevenLabsTTS is unavailable (ELEVENLABS_API_KEY missing or invalid).")

# Test 2: Multi-provider selector routing
print("\n--- Test 1.2: TTS Selector Routing ---")
selector = TTSSelector()
print(f"TTSSelector status: {selector.get_status()}")

selector_out = os.path.join(OUT, "tts_selector_sample.mp3")
sel_result = selector.execute({
    "text": "The selector abstracts multiple speech providers transparently.",
    "output_path": selector_out,
})
print(f"Selector execution success: {sel_result.success}")
if sel_result.success:
    provider_used = sel_result.data.get("provider", "unknown")
    print(f"Provider routed: {provider_used}, output: {selector_out}")
else:
    print(f"Selector error or note: {sel_result.error}")
