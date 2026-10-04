#!/usr/bin/env python3
"""QA Test 02: Image generation — generate visuals via ImageSelector and inspect outputs.

Validates image generation providers (FLUX, OpenAI, Recraft, Google Imagen) through ImageSelector.
Outputs are saved to tests/qa/output/ for visual QA.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lib.env_loader import load_env
load_env()

from tools.graphics.image_selector import ImageSelector

OUT = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUT, exist_ok=True)

IMG_OUT = os.path.join(OUT, "sample_scene.png")

print("=== QA Test 02: Image Generation ===")

selector = ImageSelector()
status = selector.get_status()
print(f"ImageSelector status: {status}")

prompt = (
    "clean professional flat illustration, corporate style, white background, "
    "a creative team collaborating on an animated video production workflow, modern geometric shapes"
)

result = selector.execute({
    "prompt": prompt,
    "output_path": IMG_OUT,
    "aspect_ratio": "16:9",
})

print(f"Execution success: {result.success}")
if result.success:
    provider = result.data.get("provider", "unknown")
    print(f"Provider routed: {provider}")
    print(f"Generated image: {IMG_OUT} ({os.path.getsize(IMG_OUT)} bytes)")
else:
    print(f"Generation error or note: {result.error}")
