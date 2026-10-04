---
name: synthetic-ui-recording
description: Guide for synthetic application UI demonstrations using Remotion `ScreenshotScene`.
license: MIT
---

# Synthetic UI Recording (Remotion ScreenshotScene)

**Decision this skill answers:** When a video brief requires demonstrating a graphical software interface (e.g. SaaS dashboard, web application, IDE, settings modal, or chat tool) — do I **record a live browser/screen session** (via Playwright or OS screen capture), or do I **synthesize it over a static screenshot** using the `ScreenshotScene` component?

> **Heuristic:** If the demonstration can be expressed as targeted user interactions (cursor movement, clicks, typed input, highlighted regions, tooltips, or chat bubble reveals) over 1–3 high-resolution screenshots, synthesize with `ScreenshotScene`. It avoids browser automation flakes, login flows, credential exposure, and network latency jitter.

---

## Why this exists

Live browser recording (Playwright/Puppeteer) frequently suffers from:
1. Dynamic page layout shifts and unpredictable render timings
2. Network latency during API calls or asset loads
3. Login barriers, two-factor auth prompts, and session expiry
4. Cluttered or sensitive data in live environments

`ScreenshotScene` in Remotion (`remotion-composer/src/components/ScreenshotScene.tsx`) provides an alternative:
* Take a single, clean static image (PNG/JPG) of the application UI.
* Define a sequence of interaction steps using normalized coordinates (`[x, y]` and `{ x, y, w, h }` from `0.0` to `1.0`).
* Render deterministic cursor movement, spring-damped click pulses, smooth typing simulation, chat bubble streams, highlight focus boxes, and callout balloons.
* The output is visually indistinguishable from real screen captures for 10–30s feature showcases.

---

## When to use ScreenshotScene

### YES, use ScreenshotScene when:
* You have a clean screenshot of the application interface or dashboard
* The interaction focuses on specific UI elements (e.g., clicking a button, filling a form field, focusing a card)
* You want frame-accurate synchronization with narration audio beats
* The demo needs callouts, focus rings, or explanatory tooltips layered directly over the controls
* You want reproducible 60fps/30fps 1080p output with no compression artifacts from video captures

### NO, capture a live recording when:
* The demo requires complex multi-page navigation across 5+ different screens
* The workflow relies on continuous physics, webgl, canvas animations, or complex scrolling lists
* The user specifically requested a real live capture of their desktop

---

## The Coordinate System

Everything in `ScreenshotScene` is **normalized between 0.0 and 1.0**:
* `[0, 0]` is top-left of the image.
* `[1, 1]` is bottom-right of the image.
* Coordinates are automatically mapped against the letterbox contain-fit rectangle of `backgroundImage`, ensuring overlays match pixel positions regardless of canvas aspect ratio (16:9 vs 9:16).

```
(0,0) ------------------------- (1,0)
|                                   |
|       [x, y] cursor position      |
|       { x, y, w, h } region       |
|                                   |
(0,1) ------------------------- (1,1)
```

---

## Step Primitives & Authoring Reference

### 1. `cursor_move`
Moves the cursor smoothly to a target point.
```json
{
  "kind": "cursor_move",
  "to": [0.65, 0.42],
  "durationSeconds": 0.8
}
```

### 2. `click_pulse`
Emits an expanding, spring-damped click wave under the cursor.
```json
{
  "kind": "click_pulse",
  "at": [0.65, 0.42],
  "durationSeconds": 0.4,
  "color": "#2563EB"
}
```

### 3. `type_into`
Types text character-by-character into an input box region.
```json
{
  "kind": "type_into",
  "region": { "x": 0.25, "y": 0.38, "w": 0.50, "h": 0.05 },
  "text": "npm install @openmontage/core",
  "typeSpeed": 0.04,
  "fontSize": 0.024
}
```

### 4. `highlight_box`
Draws a glowing focus outline around a UI element to draw the viewer's eye.
```json
{
  "kind": "highlight_box",
  "region": { "x": 0.70, "y": 0.12, "w": 0.18, "h": 0.06 },
  "durationSeconds": 2.0,
  "color": "#10B981",
  "pulses": 2
}
```

### 5. `callout_balloon`
Pops an explanatory tooltip balloon pointing directly at a UI element.
```json
{
  "kind": "callout_balloon",
  "anchor": [0.79, 0.15],
  "position": "bottom",
  "text": "1-click export to 1080p MP4",
  "durationSeconds": 2.5
}
```

### 6. `typing_dots`
Displays animated bouncing dots indicating an AI or system thought state.
```json
{
  "kind": "typing_dots",
  "at": [0.30, 0.60],
  "durationSeconds": 1.2
}
```

### 7. `bubble_append`
Animates a new chat bubble popping into the interface (supports streaming word-by-word reveal).
```json
{
  "kind": "bubble_append",
  "region": { "x": 0.20, "y": 0.55, "w": 0.60, "h": 0.15 },
  "text": "Here is the summary of your quarterly video output...",
  "role": "assistant",
  "stream": true,
  "durationSeconds": 2.0
}
```

### 8. `pause`
Holds the scene to let the viewer absorb the state or match voiceover timing.
```json
{
  "kind": "pause",
  "seconds": 1.5
}
```

---

## Cut Specification in `scene_plan` / `edit_decisions`

In an OpenMontage scene plan or edit decision cut:

```json
{
  "type": "screenshot_scene",
  "in_seconds": 12.0,
  "out_seconds": 22.0,
  "screenshotData": {
    "backgroundImage": "assets/images/app_dashboard.png",
    "backgroundSize": { "width": 1920, "height": 1080 },
    "cursorStartAt": [0.85, 0.10],
    "steps": [
      { "kind": "cursor_move", "to": [0.50, 0.45], "durationSeconds": 0.8 },
      { "kind": "click_pulse", "at": [0.50, 0.45], "durationSeconds": 0.3 },
      { "kind": "highlight_box", "region": { "x": 0.20, "y": 0.40, "w": 0.60, "h": 0.30 }, "durationSeconds": 3.0 },
      { "kind": "callout_balloon", "anchor": [0.50, 0.40], "position": "top", "text": "Real-time pipeline timeline", "durationSeconds": 2.5 },
      { "kind": "pause", "seconds": 1.0 }
    ]
  }
}
```
