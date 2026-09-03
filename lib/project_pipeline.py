"""Internal Studio teaching/demo runner (not a generic production executor).

This module remains available for a deliberately marked fixture while the
manifest-faithful agent executor is built.  Backlot must never route an
ordinary project here: the explicit marker/allow-list is enforced both by the
API and by the functions below.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import math
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import edge_tts

from lib.approval_contracts import build_checkpoint_approval
from lib.checkpoint import init_project, read_checkpoint, write_checkpoint
from lib.demo_runner import assert_internal_demo_project
from lib.events import emit_event
from lib.audio_assembly import assemble_audio_segments
from lib.voice_contracts import (
    VoiceSegmentCache,
    normalize_voice_identity,
    plan_narration_segments,
    require_voice_sample_approval,
)
from lib.music_contracts import append_music_decision, normalize_music_source
from lib.paths import PROJECTS_DIR, REPO_ROOT
from schemas.artifacts import validate_artifact
from tools.audio.openai_tts import OpenAITTS
from tools.video.video_compose import VideoCompose


# Two seconds remains the minimum authored visual cadence for the studio, but
# lesson slides are no longer forced to fit that cadence.  The narrator's
# words determine how long a beat remains on screen, with a short breathing
# space before the next idea begins.
VISUAL_BEAT_SECONDS = 2.0
CALM_WORDS_PER_MINUTE = 112
MIN_SLIDE_SECONDS = 3.5
SLIDE_BREATH_SECONDS = 0.65
ILEARNZED_SLIDE_BACKGROUND = "#061F18"
ILEARNZED_SLIDE_SURFACE = "#0C3B2D"
ILEARNZED_SLIDE_ACCENT = "#A7F36B"
ILEARNZED_SLIDE_TEXT = "#F3F8F1"
ILEARNZED_SLIDE_MUTED = "#BBD0C2"
EDGE_TTS_RATE = "-12%"
OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_VOICE = "coral"
OPENAI_TTS_SPEED = 0.9
OPENAI_TTS_INSTRUCTIONS = (
    "Speak as a warm, clear, calm, knowledgeable teacher for secondary-school learners. "
    "Use natural conversational pacing, gentle emphasis on important terms, and brief pauses "
    "after questions and conclusions. Explain the idea rather than reading a list. Sound human, "
    "audible, and reassuring; never like a trailer, salesperson, or rushed announcer."
)
BEAT_SCENE_TYPES = [
    "text_card", "animation", "diagram", "broll", "generated",
    "text_card", "diagram", "animation", "broll", "generated",
    "text_card", "diagram", "animation", "broll", "text_card",
]
BEAT_MOTIONS = [
    "zoom-in", "pan-right", "draw-on", "ken-burns-slow-zoom", "float",
    "zoom-out", "pan-left", "parallax", "parallax", "pulse",
    "zoom-in", "pan-right", "float", "ken-burns-slow-zoom", "zoom-out",
]
# A lesson should feel like one explanation, not a slideshow preset sampler.
# Use a soft crossfade for normal beats and reserve a restrained directional
# move for the few boundaries where the lesson genuinely changes section.
BEAT_TRANSITIONS = ["dissolve"]
BEAT_SHOT_SIZES = ["wide", "medium", "close_up", "insert", "medium_close"]
BEAT_CAMERA_MOVES = ["dolly_in", "pan_right", "zoom_in", "tracking_left", "zoom_out"]
TEACHER_LOOP = [
    "orientation",
    "read",
    "explanation",
    "break_down_visual",
    "connect",
    "emphasis",
    "recall",
    "transition",
]


EPIDEMIOLOGY_TEACHING_SPECS: list[dict[str, Any]] = [
    {
        "objective": "Define epidemiology as the study of health patterns in populations.",
        "complexity": 0.35,
        "importance": 0.95,
        "narration": "Let’s start with the big idea. Epidemiology is really about understanding health in groups of people, not just in one individual patient. Instead of asking, \"What happened to this person?\" we step back and ask, \"What is happening across this whole population?\" The people you see on the right represent that wider group. And once we start looking at populations, three very important questions immediately come up.",
        "diagram_explanation": "The people form a population; the connecting line represents patterns that can become evidence.",
        "visual_target": "population-cluster",
        "read_target": "bullet-1",
        "key_takeaway": "Epidemiology studies patterns of health in groups of people.",
        "transition_type": "continuation",
        "transition_narration": "Keep that population view in mind as we ask the first questions.",
        "recall": None,
    },
    {
        "objective": "Use person, place, and time to organize the first description of a health event.",
        "complexity": 0.45,
        "importance": 0.9,
        "narration": "The first questions are simple, but very powerful: Who is affected? Where is it happening? And when did it start or change? These three questions help us organize almost any epidemiological investigation. We are trying to understand the pattern before we try to explain it. Once we know who, where, and when, we can begin deciding exactly what we mean by a case.",
        "diagram_explanation": "WHO, WHERE, and WHEN are connected because each describes a different dimension of the same pattern.",
        "visual_target": "who-where-when",
        "read_target": "bullet-1",
        "key_takeaway": "Start an investigation by describing who, where, and when.",
        "transition_type": "elaboration",
        "transition_narration": "Description gives us a pattern; the next step is to make that pattern consistent.",
        "recall": {"concept": "population view", "from_slide": "scene_1"},
    },
    {
        "objective": "Connect person, place, and time as the core descriptive dimensions.",
        "complexity": 0.5,
        "importance": 0.88,
        "narration": "This is where person, place, and time come together. Think of them as three different lenses looking at the same health problem. Person tells us which groups are affected. Place tells us where the pattern is occurring. And time tells us when it started, peaked, or changed. When we combine all three, the picture becomes much clearer. The next step is to make sure we are all counting cases in the same way.",
        "diagram_explanation": "The triangle shows three dimensions surrounding one pattern; none of the dimensions should be read in isolation.",
        "visual_target": "person-place-time-triangle",
        "read_target": "bullet-1",
        "key_takeaway": "A useful description combines person, place, and time.",
        "transition_type": "problem_solution",
        "transition_narration": "A described pattern still needs a consistent rule for deciding who counts.",
        "recall": {"concept": "who, where, and when", "from_slide": "scene_2"},
    },
    {
        "objective": "Explain why a case definition is needed for consistent classification.",
        "complexity": 0.6,
        "importance": 0.92,
        "narration": "Before we start counting, we need a clear case definition. In simple terms, we need to decide who qualifies as a case and who does not. That definition may include symptoms, laboratory results, location, or a particular time period. The important thing is consistency. If two investigators assess the same person, they should ideally reach the same conclusion. Once the definition is clear, counting becomes much more reliable.",
        "diagram_explanation": "The funnel narrows broad criteria into a shared case classification so the same rule can be used repeatedly.",
        "visual_target": "case-definition-funnel",
        "read_target": "bullet-1",
        "key_takeaway": "A case definition makes classification consistent before counting begins.",
        "transition_type": "cause_effect",
        "transition_narration": "Once cases are defined consistently, we can count them before making comparisons.",
        "recall": {"concept": "descriptive dimensions", "from_slide": "scene_3"},
    },
    {
        "objective": "Distinguish raw case counts from comparable rates.",
        "complexity": 0.65,
        "importance": 0.92,
        "narration": "Now that we know what counts as a case, we can start counting. But here is an important warning: raw numbers can sometimes mislead us. One group may have more cases simply because it has a much larger population. So before saying one group is at greater risk, we need to look at the number of cases in relation to the size of the population. That is what allows us to make a fair comparison.",
        "diagram_explanation": "The bars show counts, while the formula underneath reminds us that counts become rates only after population size is included.",
        "visual_target": "case-count-bars",
        "read_target": "bullet-1",
        "key_takeaway": "A count is a starting point; a rate adds the population needed for comparison.",
        "transition_type": "new_section",
        "transition_narration": "With the difference between counts and rates clear, we can separate new cases from existing cases.",
        "recall": {"concept": "case definition", "from_slide": "scene_4"},
    },
    {
        "objective": "Define incidence as new cases occurring during a specified time period.",
        "complexity": 0.65,
        "importance": 0.9,
        "narration": "One of the first measures we use is incidence. Incidence focuses on new cases. It asks: during a particular period of time, how many people developed the disease or condition? So if we follow a population over time, every new case that appears contributes to incidence. In simple terms, incidence helps us understand how quickly new cases are occurring.",
        "diagram_explanation": "The timeline adds only cases that begin during the period, making the time window part of the measure.",
        "visual_target": "incidence-new-case-timeline",
        "read_target": "bullet-1",
        "key_takeaway": "Incidence measures new cases over a defined time period.",
        "transition_type": "contrast",
        "transition_narration": "Incidence follows new cases; prevalence gives us the wider burden already present.",
        "recall": {"concept": "count versus rate", "from_slide": "scene_5"},
    },
    {
        "objective": "Define prevalence as the existing burden of a condition at a point or period.",
        "complexity": 0.65,
        "importance": 0.9,
        "narration": "Prevalence looks at something slightly different. Instead of focusing only on new cases, it looks at everyone who currently has the condition. So prevalence helps us understand the overall burden of disease in a population at a particular point in time, or over a defined period. This is especially useful when planning health services, because it tells us how many people may currently need care or support.",
        "diagram_explanation": "The highlighted dots are all existing cases inside the population box, not only cases that started recently.",
        "visual_target": "prevalence-existing-cases",
        "read_target": "bullet-1",
        "key_takeaway": "Prevalence describes the existing burden at a defined time.",
        "transition_type": "cause_effect",
        "transition_narration": "Whether we count new or existing cases, the population behind the count keeps the comparison honest.",
        "recall": {"concept": "incidence", "from_slide": "scene_6"},
    },
    {
        "objective": "Explain the numerator and denominator as the structure of a comparable rate.",
        "complexity": 0.75,
        "importance": 0.95,
        "narration": "This is where the denominator becomes very important. We take the number of cases and relate it to the population they came from. So we have cases on top and population underneath. Without the denominator, we are only looking at counts. But once we include the population, we can calculate a rate or proportion and compare groups more fairly. In other words, the denominator gives the number context.",
        "diagram_explanation": "The numerator counts cases and the denominator describes the population that produced that count; together they create context.",
        "visual_target": "denominator-fraction",
        "read_target": "bullet-1",
        "key_takeaway": "The denominator puts every case count in the context of its population.",
        "transition_type": "application",
        "transition_narration": "Now we can use expected levels and observed counts to recognize an unusual pattern.",
        "recall": {"concept": "prevalence", "from_slide": "scene_7"},
    },
    {
        "objective": "Recognize an outbreak as an observed level above the expected pattern, pending investigation.",
        "complexity": 0.8,
        "importance": 0.95,
        "narration": "Now imagine we are monitoring cases over time. Usually, there is some level of disease that we expect to see. The dashed line on the slide represents that expected level. If the actual number of cases suddenly rises above what we normally expect, that becomes a signal. It does not automatically tell us the cause, but it tells us something unusual may be happening and we need to investigate further.",
        "diagram_explanation": "The dashed expected line provides a reference while the rising bars show observed cases that need confirmation and investigation.",
        "visual_target": "outbreak-observed-vs-expected",
        "read_target": "bullet-1",
        "key_takeaway": "An unusual rise is an investigation signal, not automatic proof of cause.",
        "transition_type": "elaboration",
        "transition_narration": "Before asking why the rise happened, describe exactly how the pattern is distributed.",
        "recall": {"concept": "denominator", "from_slide": "scene_8"},
    },
    {
        "objective": "Use descriptive epidemiology to map patterns across time, place, and person.",
        "complexity": 0.7,
        "importance": 0.9,
        "narration": "Once we notice that signal, we go back to person, place, and time. We ask: When did cases begin to rise? Where are most of them occurring? Which groups seem to be most affected? This is called descriptive epidemiology. At this stage, our job is to describe what we are seeing as clearly as possible. We are not yet proving why it happened. We are building the picture first.",
        "diagram_explanation": "The grid is a compact map of the three descriptive dimensions, helping investigators see concentration and change before testing explanations.",
        "visual_target": "descriptive-grid",
        "read_target": "bullet-1",
        "key_takeaway": "Describe the pattern first; description is not the same as proving a cause.",
        "transition_type": "contrast",
        "transition_narration": "Description tells us what is happening; analysis asks whether an exposure is related to the outcome.",
        "recall": {"concept": "person, place, and time", "from_slide": "scene_3"},
    },
    {
        "objective": "Explain analytical epidemiology as a comparison of exposure groups and outcomes.",
        "complexity": 0.75,
        "importance": 0.9,
        "narration": "Once the pattern is clear, we can start asking why it might be happening. One way we do that is by comparing exposure between groups. For example, one group may have been exposed to a particular food, environment, behaviour, or risk factor, while another group was not. We then compare their outcomes. If the exposed group experiences more disease, that gives us an important clue, although it still does not automatically prove causation.",
        "diagram_explanation": "The arrow connects exposure to outcome as an analytical question; it must not be mistaken for proof of causation.",
        "visual_target": "exposure-to-outcome-arrow",
        "read_target": "bullet-1",
        "key_takeaway": "Analysis compares exposure groups and outcomes while keeping causal claims cautious.",
        "transition_type": "application",
        "transition_narration": "After setting up the comparison, we can summarize how the risks differ between groups.",
        "recall": {"concept": "descriptive pattern", "from_slide": "scene_10"},
    },
    {
        "objective": "Interpret a risk ratio as a comparison of risk between two groups.",
        "complexity": 0.85,
        "importance": 0.95,
        "narration": "Now we want to measure how strong that relationship is. One common measure is the risk ratio. We compare the risk in one group with the risk in another group. If both groups have the same risk, the ratio will be around one. If one group has a higher risk, the ratio becomes larger. This helps us describe the strength of an association, but we still need to ask whether that association is trustworthy.",
        "diagram_explanation": "The two bars represent risk in separate groups; the ratio compares their heights after each risk has been calculated.",
        "visual_target": "risk-ratio-bars",
        "read_target": "bullet-1",
        "key_takeaway": "A risk ratio compares group risks; it does not remove bias or prove causation by itself.",
        "transition_type": "problem_solution",
        "transition_narration": "A useful comparison still needs a quality check for forces that could shift the result.",
        "recall": {"concept": "exposure and outcome", "from_slide": "scene_11"},
    },
    {
        "objective": "Identify selection, measurement, and confounding as threats to interpretation.",
        "complexity": 0.85,
        "importance": 0.95,
        "narration": "This is where bias and confounding become important. Even when we see an association, the result can sometimes be distorted. Maybe the wrong people were selected for the study. Maybe information was measured differently between groups. Or maybe another factor influenced both the exposure and the outcome. So before accepting a result, we always ask: Could something else be affecting what we are seeing?",
        "diagram_explanation": "The target represents the intended truth, while the offset path reminds us that design, measurement, or confounding can shift an estimate.",
        "visual_target": "bias-target",
        "read_target": "bullet-1",
        "key_takeaway": "Check what could shift an estimate before turning an association into action.",
        "transition_type": "problem_solution",
        "transition_narration": "Once the signal and its quality have been considered, evidence can guide a proportionate response.",
        "recall": {"concept": "risk ratio", "from_slide": "scene_12"},
    },
    {
        "objective": "Connect confirmed evidence to prevention, response, and monitoring.",
        "complexity": 0.75,
        "importance": 0.92,
        "narration": "Once we are confident in the evidence, we can use it to guide action. That action might involve prevention, treatment, surveillance, education, or another public health response. But the process does not stop once we intervene. We also need to monitor what happens next. Did the number of cases decrease? Did the intervention work? Do we need to adjust the response? Epidemiology is therefore not only about studying problems; it is also about improving decisions.",
        "diagram_explanation": "The shield groups prevention, response, and monitoring as connected safeguards rather than a single one-time intervention.",
        "visual_target": "prevention-shield",
        "read_target": "bullet-1",
        "key_takeaway": "Evidence becomes useful when it supports action and continued monitoring.",
        "transition_type": "summary",
        "transition_narration": "Let us close by recalling how each step moved us from a question to healthier populations.",
        "recall": {"concept": "bias check", "from_slide": "scene_13"},
    },
    {
        "objective": "Synthesize the full epidemiology workflow from question to population health action.",
        "complexity": 0.9,
        "importance": 1.0,
        "narration": "So let’s bring everything together. We started by asking who, where, and when. We defined what counted as a case, counted those cases, used the population to make fair comparisons, and then looked for possible associations. We also checked for bias before using the evidence to make a decision.\n\nAnd that is really the heart of epidemiology. We take observations, turn them into evidence, and then use that evidence to protect and improve the health of populations.\n\nIf there is one idea I want you to remember, it is this: epidemiology helps us move from simply noticing a health problem to understanding it well enough to do something useful about it.",
        "diagram_explanation": "The final flow connects evidence, decision, and action to show why the earlier measurement and quality steps matter.",
        "visual_target": "evidence-to-action-flow",
        "read_target": "slide-summary",
        "key_takeaway": "Sound evidence is a bridge from facts to practical action.",
        "transition_type": "conclusion",
        "transition_narration": "That is the epidemiology journey: ask clearly, measure carefully, and act responsibly.",
        "recall": {"concept": "population health", "from_slide": "scene_1"},
    },
]


class ContentTemplateRequiredError(RuntimeError):
    """Raised when the quarantined demo runner lacks a subject-specific plan."""


def _is_epidemiology_topic(title: str, topic: str) -> bool:
    """Recognise only the explicit dogfood subject, not negated user text."""

    value = f"{title} {topic}".lower()
    if "not epidemiol" in value or "non-epidemiol" in value:
        return False
    return "epidemiol" in value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _teacher_event_templates(beat: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the ordered teaching loop for one slide.

    These are semantic cues, not a second opaque script.  They let the
    narration timeline and Remotion renderer refer to the same teaching unit:
    orient the learner, read the visible idea, explain it, inspect the visual,
    connect it to memory, emphasize the takeaway, and bridge to the next slide.
    """
    spec = beat
    events: list[dict[str, Any]] = [
        {
            "type": "orientation",
            "text": f"Focus on the teaching question: {beat['title']}.",
            "emphasis": "low",
            "visual_target": "slide-title",
            "weight": 0.10,
        },
        {
            "type": "read",
            "text": f"Verbatim anchor: {beat.get('read_text') or beat['bullets'][0]}.",
            "emphasis": "medium",
            "visual_target": beat.get("read_target") or "bullet-1",
            "weight": 0.14,
        },
        {
            "type": "explanation",
            "text": str(beat.get("narration") or ""),
            "emphasis": "medium",
            "visual_target": "main-explanation",
            "weight": 0.28,
        },
        {
            "type": "break_down_visual",
            "text": str(beat.get("diagram_explanation") or "Explain the diagram from its parts to its meaning."),
            "emphasis": "high",
            "visual_target": beat.get("visual_target") or f"diagram-{beat.get('diagram', 'concept')}",
            "weight": 0.20,
        },
        {
            "type": "connect",
            "text": str(beat.get("connection_narration") or "Connect this idea to the lesson so far."),
            "emphasis": "medium",
            "visual_target": "lesson-connection",
            "weight": 0.10,
        },
        {
            "type": "emphasis",
            "text": f"Remember: {beat.get('key_takeaway') or beat['bullets'][0]}.",
            "emphasis": "high",
            "visual_target": "takeaway",
            "weight": 0.12,
        },
    ]
    recall = beat.get("recall")
    if recall:
        events.append({
            "type": "recall",
            "text": f"Recall the earlier idea: {recall['concept']}.",
            "emphasis": "medium",
            "visual_target": "lesson-memory",
            "weight": 0.08,
        })
    events.append({
        "type": "transition",
        "text": str(beat.get("transition_narration") or "Connect this idea to the next step."),
        "emphasis": "medium",
        "visual_target": "next-slide-bridge",
        "weight": 0.10,
    })
    return events


def _apply_teacher_framework(
    beats: list[dict[str, Any]],
    *,
    allow_domain_defaults: bool = False,
) -> list[dict[str, Any]]:
    """Attach reusable teacher metadata to every generated slide."""
    is_epidemiology = any(
        str(beat.get("title")) == "What is epidemiology?"
        for beat in beats
    ) and allow_domain_defaults
    by_title = (
        {
            str(beat.get("title")): spec
            for beat, spec in zip(beats, EPIDEMIOLOGY_TEACHING_SPECS)
        }
        if is_epidemiology
        else {}
    )
    for beat in beats:
        spec = by_title.get(str(beat.get("title")))
        if spec:
            beat.update({
                "objective": spec["objective"],
                "complexity": float(spec["complexity"]),
                "importance": float(spec["importance"]),
                "narration": spec["narration"],
                "diagram_explanation": spec["diagram_explanation"],
                "visual_target": spec["visual_target"],
                "read_target": spec["read_target"],
                "read_text": spec.get("read_text") or beat.get("bullets", ["the key idea"])[0],
                "key_takeaway": spec["key_takeaway"],
                "transition_type": spec["transition_type"],
                "transition_narration": spec["transition_narration"],
                "recall": spec["recall"],
            })
        else:
            beat.setdefault("objective", f"Understand and apply {beat.get('title', 'this idea')}.")
            beat.setdefault("complexity", 0.45)
            beat.setdefault("importance", 0.7)
            beat.setdefault("diagram_explanation", "Read the visual from left to right and connect each part to the key idea.")
            beat.setdefault("visual_target", f"diagram-{beat.get('diagram', 'concept')}")
            beat.setdefault("read_target", "bullet-1")
            beat.setdefault("read_text", beat.get("bullets", ["the key idea"])[0])
            beat.setdefault("key_takeaway", beat.get("bullets", ["Keep the key idea in mind."])[0])
            beat.setdefault("transition_type", "continuation")
            beat.setdefault("transition_narration", "Keep this idea in mind as we apply it next.")
            beat.setdefault("recall", None)
        beat["event_templates"] = _teacher_event_templates(beat)
    return beats


def _visual_action_for_event(event_type: str) -> str:
    """Map a teaching event to the closest deterministic Remotion action."""
    return {
        "orientation": "reveal",
        "read": "highlight",
        "explanation": "dim-others",
        "break_down_visual": "trace",
        "emphasis": "pulse",
        "recall": "underline",
        "transition": "pan",
    }.get(event_type, "highlight")


def _materialize_teaching_events(
    beats: list[dict[str, Any]],
    narration_timeline: list[dict[str, Any]],
    fps: int = 30,
) -> list[dict[str, Any]]:
    """Give every semantic teaching cue exact seconds and frame boundaries."""
    for index, beat in enumerate(beats):
        segment = narration_timeline[index] if index < len(narration_timeline) else {}
        slide_start = float(segment.get("start_seconds", 0.0))
        slide_end = float(segment.get("end_seconds", slide_start))
        slide_duration = max(0.01, slide_end - slide_start)
        templates = beat.get("event_templates") or _teacher_event_templates(beat)
        total_weight = sum(max(0.01, float(item.get("weight", 1.0))) for item in templates)
        cursor = slide_start
        events: list[dict[str, Any]] = []
        for event_index, template in enumerate(templates, start=1):
            if event_index == len(templates):
                end = slide_end
            else:
                share = max(0.01, float(template.get("weight", 1.0))) / total_weight
                end = min(slide_end, cursor + slide_duration * share)
            start_frame = round(cursor * fps)
            end_frame = max(start_frame + 1, round(end * fps))
            duration = max(1 / fps, end - cursor)
            event_type = str(template.get("type") or "explanation")
            event = {
                "eventId": f"scene_{index + 1}-event-{event_index}",
                "slideId": f"scene_{index + 1}",
                "type": event_type,
                "text": str(template.get("text") or ""),
                "startTime": round(cursor, 3),
                "duration": round(duration, 3),
                "start_seconds": round(cursor, 3),
                "end_seconds": round(end, 3),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "emphasis": str(template.get("emphasis") or "medium"),
                "visualTarget": str(template.get("visual_target") or "main-explanation"),
                "visualAction": {
                    "targetId": str(template.get("visual_target") or "main-explanation"),
                    "action": _visual_action_for_event(event_type),
                },
            }
            events.append(event)
            cursor = end
        beat["events"] = events
    return beats


def _build_teaching_plan(
    title: str,
    beats: list[dict[str, Any]],
    narration_timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create the inspectable lesson artifact used by Backlot and QA."""
    slides = []
    concepts = []
    for index, beat in enumerate(beats):
        segment = narration_timeline[index] if index < len(narration_timeline) else {}
        concepts.append({
            "id": f"concept_{index + 1}",
            "name": beat.get("title"),
            "firstIntroducedAt": f"scene_{index + 1}",
            "importance": beat.get("importance", 0.7),
            "appearances": [f"scene_{index + 1}"],
            "lastMentionedAt": f"scene_{index + 1}",
        })
        slides.append({
            "slide_id": f"scene_{index + 1}",
            "title": beat.get("title"),
            "objective": beat.get("objective"),
            "complexity": beat.get("complexity"),
            "importance": beat.get("importance"),
            "estimated_duration_seconds": round(float(segment.get("end_seconds", 0.0)) - float(segment.get("start_seconds", 0.0)), 3),
            "events": beat.get("events", []),
            "key_takeaway": beat.get("key_takeaway"),
            "recall": beat.get("recall"),
            "transition": {
                "type": beat.get("transition_type"),
                "narration": beat.get("transition_narration"),
            },
            "diagram": {
                "kind": beat.get("diagram"),
                "explanation": beat.get("diagram_explanation"),
                "visual_target": beat.get("visual_target"),
            },
        })
    return {
        "version": "1.0",
        "skill": "video-reader-ai-teacher",
        "title": title,
        "audience": {"level": "mixed_secondary", "depth_mode": "DETAILED"},
        "teacher_loop": TEACHER_LOOP,
        "timing": {
            "mode": "content_led",
            "calm_words_per_minute": CALM_WORDS_PER_MINUTE,
            "slide_breath_seconds": SLIDE_BREATH_SECONDS,
            "fps": 30,
        },
        "lesson_memory": {
            "concepts": concepts,
            "major_questions": ["Who is affected?", "Where is it happening?", "When did it change?"],
            "examples_used": [],
            "analogies_used": [],
            "jokes_used": [],
            "major_takeaways": [beat.get("key_takeaway") for beat in beats],
            "previous_slide_ids": [f"scene_{i}" for i in range(1, len(beats))],
            "unresolved_ideas": [],
        },
        "slides": slides,
        "renderer_note": "Teaching cues are frame-addressable. Remotion renders structured text and semantic diagram primitives with staged reveals and purposeful motion; SVG fallbacks remain available for inspection and export.",
    }


def _build_lesson_beats(
    title: str,
    topic: str,
    beat_count: int,
    template_beats: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return one teachable slide payload per visual beat.

    The epidemiology set is intentionally concrete for the current iLearnZed
    dogfood project.  Other topics must provide an explicit subject-specific
    ``template_beats`` payload; a generic "step N" or teacher filler is never
    fabricated by the runner.
    """
    is_epidemiology = bool(not template_beats and _is_epidemiology_topic(title, topic))
    if is_epidemiology:
        beats = [
            ("What is epidemiology?", "FOUNDATION", ["Studies health in populations", "Looks for patterns", "Uses evidence to guide action"], "Epidemiology is the study of health in populations. It looks for patterns that help us act.", "population"),
            ("The first questions", "FOUNDATION", ["Who is affected?", "Where is it happening?", "When did it change?"], "We begin with three questions: who is affected, where is it happening, and when did it change?", "questions"),
            ("Person · place · time", "FOUNDATION", ["Describe the person", "Locate the place", "Track the time"], "We describe each pattern through person, place, and time. Together, they show us where attention is needed.", "three_part"),
            ("Define the case", "MEASUREMENT", ["Set clear criteria", "Decide who counts", "Keep classification consistent"], "A case definition sets clear criteria, so every person is classified in the same way.", "case_definition"),
            ("Count before comparing", "MEASUREMENT", ["Start with case counts", "Add the population", "Then calculate a rate"], "We count cases first, then add the population at risk. That lets us calculate a useful rate.", "count_rates"),
            ("Incidence", "MEASUREMENT", ["Counts new cases", "Uses a time period", "Signals recent risk"], "Incidence counts new cases during a defined period, so it helps us see recent risk.", "incidence"),
            ("Prevalence", "MEASUREMENT", ["Counts existing cases", "Shows the current burden", "Supports service planning"], "Prevalence counts all existing cases. It shows the burden services must support today.", "prevalence"),
            ("Use a denominator", "MEASUREMENT", ["Numerator: cases", "Denominator: population", "The rate becomes comparable"], "A denominator tells us the population behind the count. That makes rates comparable across groups.", "denominator"),
            ("Recognize an outbreak", "PATTERNS", ["Estimate what is expected", "Observe the actual count", "Investigate the difference"], "An outbreak becomes visible when observed cases rise above what we expected. That difference prompts investigation.", "outbreak"),
            ("Describe the pattern", "EVIDENCE", ["Map time trends", "Compare places", "Profile affected people"], "Descriptive studies map patterns across time, place, and person before we ask why they occurred.", "descriptive"),
            ("Compare exposures", "EVIDENCE", ["Define the exposure", "Compare groups", "Measure the outcome"], "Analytical studies compare exposure groups and measure whether their outcomes differ.", "analytical"),
            ("Measure association", "EVIDENCE", ["Measure risk in each group", "Compare the two risks", "Interpret the result"], "A risk ratio compares risk in one group with risk in another, helping us interpret association.", "risk_ratio"),
            ("Watch for bias", "QUALITY", ["Check selection", "Check measurement", "Check confounding"], "We check selection, measurement, and confounding because bias can shift the result away from the truth.", "bias"),
            ("Apply the evidence", "ACTION", ["Confirm the signal", "Choose prevention", "Monitor the response"], "Once the signal is confirmed, evidence supports prevention, response, and continued monitoring.", "prevention"),
            ("From facts to action", "ACTION", ["Ask a clear question", "Use sound evidence", "Act for healthier populations"], "Good epidemiology turns a clear question and sound evidence into action for healthier populations.", "action"),
        ]
        result = [
            {"title": t, "section": s, "bullets": b, "narration": n, "diagram": d}
            for t, s, b, n, d in beats
        ]
    elif template_beats:
        result = []
        for index, raw in enumerate(template_beats):
            if not isinstance(raw, dict):
                raise ContentTemplateRequiredError(
                    f"template_beats[{index}] must be an object with title, narration, bullets, and diagram"
                )
            required = ("title", "narration", "bullets", "diagram")
            missing = [field for field in required if not raw.get(field)]
            if missing:
                raise ContentTemplateRequiredError(
                    f"template_beats[{index}] is missing explicit fields: {', '.join(missing)}"
                )
            result.append({
                "title": str(raw["title"]),
                "section": str(raw.get("section") or "APPLICATION"),
                "bullets": [str(value) for value in raw["bullets"]],
                "narration": str(raw["narration"]),
                "diagram": str(raw["diagram"]),
                **{key: value for key, value in raw.items() if key not in {"title", "section", "bullets", "narration", "diagram"}},
            })
    else:
        raise ContentTemplateRequiredError(
            "No subject-specific template_beats supplied; the internal demo runner "
            "will not inject generic teacher-slide filler for a non-template topic"
        )

    for index in range(len(result), beat_count):
        if not template_beats:
            raise ContentTemplateRequiredError(
                "template_beats contains fewer beats than requested; add explicit beats "
                "instead of allowing generic filler"
            )
        raise ContentTemplateRequiredError(
            f"template_beats contains {len(result)} beats but {beat_count} are required"
        )
    return _apply_teacher_framework(result[:beat_count], allow_domain_defaults=is_epidemiology)


def _estimate_beat_durations(beats: list[dict[str, Any]]) -> list[float]:
    """Estimate calm, content-led slide durations before TTS is rendered.

    The estimate is deliberately a floor, not a hard trim target.  After TTS
    renders, ``_generate_narration`` extends any beat whose natural speech is
    longer.  This keeps the narrator from being sped up to satisfy an old
    30-second ceiling.
    """
    words_per_second = CALM_WORDS_PER_MINUTE / 60.0
    durations: list[float] = []
    for beat in beats:
        text = str(beat.get("narration") or "")
        word_count = len(text.split())
        speech_seconds = word_count / words_per_second
        durations.append(round(max(MIN_SLIDE_SECONDS, speech_seconds + SLIDE_BREATH_SECONDS), 3))
    return durations


def _timeline_duration(timeline: list[dict[str, Any]]) -> float:
    """Return the end of the last authored narration beat."""
    return round(max((float(segment.get("end_seconds", 0.0)) for segment in timeline), default=0.0), 3)


def _diagram_svg(kind: str, accent: str, light: str, ink: str) -> str:
    """Draw a compact concept diagram inside the right-hand slide panel."""
    def label(x: int, y: int, text: str, size: int = 24, color: str | None = None) -> str:
        return f'<text x="{x}" y="{y}" fill="{color or light}" font-family="Arial, sans-serif" font-size="{size}" font-weight="700" text-anchor="middle">{html.escape(text)}</text>'

    def box(x: int, y: int, w: int, h: int, text: str, fill: str = "#174B3B") -> str:
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill}" stroke="{accent}" stroke-width="3"/>{label(x + w // 2, y + h // 2 + 8, text, 24)}'

    if kind == "population":
        return "".join([label(1340, 485, "POPULATION", 28, accent), *(f'<circle cx="{x}" cy="{y}" r="30" fill="{accent}" opacity="{0.45 + ((x + y) % 3) * 0.2}"/>' for x, y in [(1190, 590), (1300, 560), (1410, 590), (1245, 700), (1360, 700), (1470, 680)]), f'<path d="M1190 820 H1500" stroke="{accent}" stroke-width="8" stroke-linecap="round"/>' , label(1345, 875, "patterns → evidence", 25)])
    if kind == "questions":
        return box(1100, 510, 190, 120, "WHO") + box(1335, 510, 190, 120, "WHERE") + box(1218, 700, 190, 120, "WHEN") + f'<path d="M1290 570 H1335 M1430 630 L1330 700 M1218 700 L1195 630" stroke="{accent}" stroke-width="6" fill="none" marker-end="url(#arrow)"/>'
    if kind == "three_part":
        return f'<path d="M1340 470 L1150 800 L1530 800 Z" fill="none" stroke="{accent}" stroke-width="8"/><circle cx="1340" cy="470" r="44" fill="{accent}"/><circle cx="1150" cy="800" r="44" fill="{accent}"/><circle cx="1530" cy="800" r="44" fill="{accent}"/>' + label(1340, 480, "PERSON", 20, ink) + label(1150, 810, "PLACE", 20, ink) + label(1530, 810, "TIME", 20, ink) + label(1340, 665, "PATTERN", 28, accent)
    if kind == "case_definition":
        return f'<path d="M1130 490 H1550 L1460 610 H1220 Z" fill="{accent}" opacity="0.18" stroke="{accent}" stroke-width="5"/><path d="M1220 610 H1460 L1410 760 H1270 Z" fill="{accent}" opacity="0.28" stroke="{accent}" stroke-width="5"/>' + label(1340, 555, "criteria", 25, accent) + label(1340, 700, "CASE", 30, accent) + label(1340, 840, "count consistently", 24)
    if kind == "count_rates":
        bars = "".join(f'<rect x="{1120 + i * 92}" y="{760 - h}" width="58" height="{h}" rx="10" fill="{accent}" opacity="{0.45 + i * 0.12}"/>' for i, h in enumerate([80, 150, 220, 290]))
        return f'<path d="M1080 800 H1570 M1080 800 V460" stroke="{light}" stroke-width="4"/>{bars}' + label(1330, 875, "cases ÷ population = rate", 25, accent)
    if kind == "incidence":
        dots = "".join(f'<circle cx="{1130 + i * 85}" cy="{650 - (i % 2) * 80}" r="18" fill="{accent}"/>' for i in [0, 1, 2, 3])
        return f'<path d="M1100 700 H1570" stroke="{light}" stroke-width="8"/>{dots}<path d="M1100 700 V500" stroke="{light}" stroke-width="4"/>' + label(1335, 810, "NEW CASES · TIME", 25, accent)
    if kind == "prevalence":
        return f'<rect x="1120" y="500" width="450" height="270" rx="30" fill="none" stroke="{accent}" stroke-width="6"/>' + "".join(f'<circle cx="{x}" cy="{y}" r="25" fill="{accent}"/>' for x, y in [(1190, 580), (1280, 650), (1380, 570), (1470, 680), (1450, 560), (1240, 730)]) + label(1345, 840, "ALL EXISTING CASES", 25, accent)
    if kind == "denominator":
        return label(1340, 525, "CASES", 34, accent) + f'<path d="M1150 575 H1530" stroke="{light}" stroke-width="8"/>' + label(1340, 710, "POPULATION", 34, accent) + label(1340, 840, "comparable rate", 25, accent)
    if kind == "outbreak":
        bars = "".join(f'<rect x="{1120 + i * 75}" y="{760 - h}" width="48" height="{h}" rx="8" fill="{accent}"/>' for i, h in enumerate([80, 100, 120, 280, 350, 430]))
        return f'<path d="M1080 610 C1200 580 1330 620 1570 600" fill="none" stroke="{light}" stroke-width="6" stroke-dasharray="14 12"/>{bars}' + label(1330, 865, "observed > expected", 25, accent)
    if kind == "descriptive":
        cells = "".join(f'<rect x="{1110 + c * 90}" y="{510 + r * 90}" width="70" height="70" rx="8" fill="{accent}" opacity="{0.25 + ((r + c) % 4) * 0.18}"/>' for r in range(3) for c in range(4))
        return cells + label(1345, 850, "TIME · PLACE · PERSON", 25, accent)
    if kind == "analytical":
        return box(1090, 560, 190, 130, "EXPOSURE") + box(1450, 560, 190, 130, "OUTCOME") + f'<path d="M1280 625 H1450" stroke="{accent}" stroke-width="10" marker-end="url(#arrow)"/>' + label(1365, 805, "compare groups", 25, accent)
    if kind == "risk_ratio":
        return f'<rect x="1140" y="620" width="100" height="180" rx="12" fill="{accent}" opacity="0.55"/><rect x="1430" y="520" width="100" height="280" rx="12" fill="{accent}"/><path d="M1090 810 H1580" stroke="{light}" stroke-width="4"/>' + label(1190, 860, "GROUP A", 22) + label(1480, 860, "GROUP B", 22) + label(1340, 465, "RISK A ÷ RISK B", 28, accent)
    if kind == "bias":
        return f'<circle cx="1340" cy="650" r="160" fill="none" stroke="{accent}" stroke-width="8"/><circle cx="1340" cy="650" r="65" fill="{accent}"/><path d="M1110 470 L1270 600" stroke="{light}" stroke-width="12" marker-end="url(#arrow)"/>' + label(1340, 875, "check what shifts the result", 24, accent)
    if kind == "prevention":
        return f'<path d="M1340 470 L1510 540 V690 C1510 790 1415 845 1340 875 C1265 845 1170 790 1170 690 V540 Z" fill="{accent}" opacity="0.22" stroke="{accent}" stroke-width="8"/><path d="M1260 675 L1315 730 L1430 590" fill="none" stroke="{accent}" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>' + label(1340, 925, "PREVENT · MONITOR · IMPROVE", 23, accent)
    return box(1090, 590, 190, 120, "EVIDENCE") + box(1250, 760, 190, 120, "DECISION") + box(1450, 590, 190, 120, "ACTION") + f'<path d="M1280 650 L1370 770 M1440 770 L1510 710" stroke="{accent}" stroke-width="7" fill="none" marker-end="url(#arrow)"/>'


def _write_lesson_slides(images_dir: Path, title: str, beats: list[dict[str, Any]]) -> list[Path]:
    """Render a clean static fallback deck matching the live Remotion slide grammar.

    The live renderer uses ``TeacherSlide`` for animated text and diagrams. These
    SVGs remain useful for thumbnails, artifact inspection, and environments that
    cannot run Remotion, so they intentionally follow the same safe grid and do
    not print the full narration over the teaching material.
    """
    def wrap_caption(text: str, max_chars: int = 72, max_lines: int = 2) -> list[str]:
        words = str(text).split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > max_chars:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        if len(lines) <= max_lines:
            return lines
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,:;-") + "…"
        return lines

    images_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    bg, panel, accent, light, muted, ink = "#061F18", "#0C3B2D", "#A7F36B", "#F3F8F1", "#BBD0C2", "#09251D"
    safe_title = html.escape((title or "OPENMONTAGE")[:48])
    for index, beat in enumerate(beats, start=1):
        section = html.escape(str(beat.get("section") or "LESSON")[:24])
        title_lines = wrap_caption(str(beat.get("title") or "Lesson point"), 27, 2)
        title_svg = "".join(
            f'<text x="135" y="{250 + line_index * 66}" fill="{light}" font-family="Arial, sans-serif" font-size="60" font-weight="700">{html.escape(line)}</text>'
            for line_index, line in enumerate(title_lines)
        )
        bullet_svg: list[str] = []
        for bullet_index, point in enumerate((beat.get("bullets") or [])[:3]):
            y = 470 + bullet_index * 112
            lines = wrap_caption(str(point), 27, 2)
            line_svg = "".join(
                f'<text x="225" y="{y + 8 + line_index * 34}" fill="{light}" font-family="Arial, sans-serif" font-size="31" font-weight="600">{html.escape(line)}</text>'
                for line_index, line in enumerate(lines)
            )
            bullet_svg.append(
                f'<circle cx="178" cy="{y - 4}" r="20" fill="{accent}"/><text x="178" y="{y + 4}" fill="{ink}" font-family="Arial, sans-serif" font-size="17" font-weight="800" text-anchor="middle">{bullet_index + 1}</text>{line_svg}'
            )
        bullets = "".join(bullet_svg)
        progress = int(1640 * index / max(1, len(beats)))
        diagram = _diagram_svg(str(beat.get("diagram") or "action"), accent, light, ink)
        title_bottom = 250 + len(title_lines) * 66
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
  <defs><marker id="arrow" markerWidth="12" markerHeight="12" refX="9" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 Z" fill="{accent}"/></marker></defs>
  <rect width="1920" height="1080" fill="{bg}"/>
  <rect x="72" y="45" width="1776" height="990" rx="34" fill="{panel}" stroke="{accent}" stroke-width="2"/>
  <text x="130" y="105" fill="{accent}" font-family="Arial, sans-serif" font-size="21" font-weight="700" letter-spacing="4">iLearnZed · VISUAL LESSON</text>
  <text x="1790" y="105" fill="{muted}" font-family="Arial, sans-serif" font-size="21" font-weight="700" text-anchor="end" letter-spacing="2">BEAT {index:02d} / {len(beats):02d}</text>
  <text x="130" y="177" fill="{accent}" font-family="Arial, sans-serif" font-size="20" font-weight="700" letter-spacing="4">{section}</text>
  {title_svg}
  <text x="130" y="{title_bottom + 16}" fill="{muted}" font-family="Arial, sans-serif" font-size="22">{safe_title} · follow the visual model</text>
  <rect x="120" y="350" width="790" height="520" rx="26" fill="{bg}" stroke="{muted}" stroke-width="2"/>
  <text x="160" y="398" fill="{muted}" font-family="Arial, sans-serif" font-size="17" font-weight="700" letter-spacing="3">KEY POINTS</text>
  {bullets}
  <text x="160" y="815" fill="{accent}" font-family="Arial, sans-serif" font-size="14" font-weight="800" letter-spacing="2">TAKEAWAY</text>
  <text x="160" y="842" fill="{muted}" font-family="Arial, sans-serif" font-size="18">{html.escape(str(beat.get("key_takeaway") or (beat.get("bullets") or ["Keep this idea in mind."])[0])[:78])}</text>
  <rect x="940" y="300" width="790" height="570" rx="26" fill="{bg}" stroke="{muted}" stroke-width="2"/>
  <text x="980" y="350" fill="{muted}" font-family="Arial, sans-serif" font-size="17" font-weight="700" letter-spacing="3">VISUAL MODEL</text>
  <text x="1690" y="350" fill="{accent}" font-family="Arial, sans-serif" font-size="14" font-weight="700" text-anchor="end">{html.escape(str(beat.get("visual_target") or "guided diagram")[:34])}</text>
  {diagram}
  <text x="130" y="965" fill="{muted}" font-family="Arial, sans-serif" font-size="14" font-weight="700" letter-spacing="2">EXPLAIN · CONNECT · RECALL</text>
  <rect x="420" y="958" width="1370" height="6" rx="3" fill="#1A4A3A"/><rect x="420" y="958" width="{progress}" height="6" rx="3" fill="{accent}"/>
</svg>'''
        path = images_dir / f"scene{index}.svg"
        path.write_text(svg, encoding="utf-8")
        paths.append(path)
    return paths


def _probe_render(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        fmt = payload.get("format", {})
        video = next((s for s in payload.get("streams", []) if s.get("codec_type") == "video"), {})
        return {
            "duration_seconds": round(float(fmt.get("duration", 0)), 3),
            "resolution": f"{video.get('width', 0)}x{video.get('height', 0)}",
            "fps": 30.0,
            "codec": video.get("codec_name", "unknown"),
        }
    except Exception:
        return {}


def _section_for_time(sections: list[dict[str, Any]], time_seconds: float) -> dict[str, Any]:
    for section in sections:
        if section["start_seconds"] <= time_seconds < section["end_seconds"]:
            return section
    return sections[-1]


def _build_script(
    title: str,
    topic: str,
    duration: float,
    script_sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    duration = max(2.0, float(duration))
    # Section boundaries scale from the final content-led duration.  Individual
    # slide timings are authoritative in the scene plan; these sections only
    # provide the broader narrative grouping used by the board and edit audit.
    boundaries = [0.0, duration * 4 / 15, duration * 8 / 15, duration * 11 / 15, duration]
    labels = [
        f"Introduction - {title}",
        "Core Principles",
        "Real-World Application",
        "Conclusion",
    ]
    is_epidemiology = bool(not script_sections and _is_epidemiology_topic(title, topic))
    if is_epidemiology:
        texts = [
            "Epidemiology studies health in populations and asks who, where, and when.",
            "Case definitions, counts, incidence, prevalence, and denominators make disease measurable.",
            "Outbreaks, descriptive studies, and analytical comparisons turn patterns into evidence.",
            "Bias checks protect interpretation, while evidence guides prevention and action.",
        ]
    elif script_sections:
        texts = []
        labels = []
        for index, section in enumerate(script_sections):
            if not isinstance(section, dict) or not str(section.get("text") or "").strip():
                raise ContentTemplateRequiredError(
                    f"script_sections[{index}] must provide explicit non-empty text"
                )
            texts.append(str(section["text"]))
            labels.append(str(section.get("label") or f"Section {index + 1}"))
        if len(texts) != 4:
            raise ContentTemplateRequiredError(
                "script_sections must contain exactly four explicit narrative sections"
            )
    else:
        raise ContentTemplateRequiredError(
            "No subject-specific content template or script_sections supplied; the "
            "internal demo runner will not fabricate generic narration"
        )
    sections = []
    delivery_cues = [
        {
            "pace": "measured",
            "energy": "curious",
            "emphasis_words": ["big idea", "populations", "who", "where", "when"],
            "pause_before_seconds": 0.15,
            "pause_after_seconds": 0.55,
            "delivery_note": "Open warmly, establish the population view, then invite the learner into the three questions.",
        },
        {
            "pace": "measured",
            "energy": "clear",
            "emphasis_words": ["case definition", "incidence", "prevalence", "denominator"],
            "pause_before_seconds": 0.2,
            "pause_after_seconds": 0.5,
            "delivery_note": "Slow down around definitions and contrasts; let each technical term land before continuing.",
        },
        {
            "pace": "conversational",
            "energy": "analytical",
            "emphasis_words": ["signal", "compare", "risk ratio", "association"],
            "pause_before_seconds": 0.15,
            "pause_after_seconds": 0.5,
            "delivery_note": "Build the reasoning step by step and make clear that association is not automatically causation.",
        },
        {
            "pace": "measured",
            "energy": "confident",
            "emphasis_words": ["bias", "action", "evidence", "populations"],
            "pause_before_seconds": 0.25,
            "pause_after_seconds": 0.8,
            "delivery_note": "Become more deliberate for the synthesis and leave space before the final takeaway.",
        },
    ]
    for i in range(4):
        section_source = script_sections[i] if script_sections and not is_epidemiology else {}
        sections.append({
            "id": f"sec_{i + 1}",
            "label": labels[i],
            "text": texts[i],
            "start_seconds": round(boundaries[i], 3),
            "end_seconds": round(boundaries[i + 1], 3),
            "speaker_directions": delivery_cues[i]["delivery_note"],
            "delivery_cues": delivery_cues[i],
            **({
                key: section_source[key]
                for key in ("claim_type", "claim_id", "claim_ids", "source_refs", "factuality_status", "risk_level", "category", "critical", "source_ref")
                if key in section_source
            } if section_source else {}),
        })
    return {
        "version": "1.0",
        "title": title,
        "total_duration_seconds": duration,
        "voice_performance": {
            "performance_intent": "Warm, calm, human teacher explaining each visual clearly before moving to the next.",
            "pacing_profile": "conversational",
            "energy_curve": "curious opening, clearer technical middle, confident and deliberate close",
            "pause_policy": "Use short pauses after setup lines and longer pauses before important contrasts, conclusions, and the final takeaway.",
            "sample_section_id": "sec_4",
            "provider_notes": {
                "openai": "Use gpt-4o-mini-tts instructions for role, pacing, emphasis, and emotional arc.",
                "edge": "Use slower rate and punctuation when OpenAI is not selected explicitly.",
            },
        },
        "sections": sections,
    }


def _build_scene_plan(
    script: dict[str, Any],
    playbook: str,
    project_id: str,
    beats: list[dict[str, Any]] | None = None,
    narration_timeline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    duration = float(script["total_duration_seconds"])
    beat_count = len(narration_timeline) if narration_timeline else max(1, math.ceil(duration / VISUAL_BEAT_SECONDS))
    scenes = []
    for index in range(beat_count):
        if narration_timeline and index < len(narration_timeline):
            start = round(float(narration_timeline[index]["start_seconds"]), 3)
            end = round(float(narration_timeline[index]["end_seconds"]), 3)
        else:
            start = round(index * VISUAL_BEAT_SECONDS, 3)
            end = round(min(duration, (index + 1) * VISUAL_BEAT_SECONDS), 3)
        section = _section_for_time(script["sections"], start)
        beat = beats[index] if beats and index < len(beats) else None
        beat_duration = max(0.01, end - start)
        previous_section_id = scenes[-1]["script_section_id"] if scenes else None
        section_boundary = index > 0 and previous_section_id != section["id"]
        boundary_transition = "slide-left" if section_boundary else "dissolve"
        scenes.append({
            "id": f"scene_{index + 1}",
            "type": BEAT_SCENE_TYPES[index % len(BEAT_SCENE_TYPES)],
            "description": f"Slide {index + 1}: {(beat or {}).get('title') or section['label']}",
            "start_seconds": start,
            "end_seconds": end,
            "script_section_id": section["id"],
            "movement": BEAT_MOTIONS[index % len(BEAT_MOTIONS)],
            "transition_in": "cut" if index == 0 else boundary_transition,
            "transition_out": "fade" if index == beat_count - 1 else boundary_transition,
            "shot_language": {
                "shot_size": BEAT_SHOT_SIZES[index % len(BEAT_SHOT_SIZES)],
                "camera_movement": BEAT_CAMERA_MOVES[index % len(BEAT_CAMERA_MOVES)],
                "lens_mm": [35, 50, 85, 24, 50][index % 5],
                "lighting_key": "natural" if index % 3 else "high_key",
                "depth_of_field": "medium",
                "color_temperature": "neutral",
            },
            "shot_intent": f"Hold the {section['label']} slide for {beat_duration:g} seconds while the narrator explains it.",
            "narrative_role": "introduce_subject" if index == 0 else ("resolution" if index == beat_count - 1 else "deliver_payload"),
            "information_role": (
                (beat or {}).get("key_takeaway")
                or (beat or {}).get("narration")
                or section["text"]
            )[:120],
        })
    return {
        "version": "1.0",
        "style_playbook": playbook,
        "scenes": scenes,
        "metadata": {
            "teaching_skill": "video-reader-ai-teacher",
            "teacher_loop": TEACHER_LOOP,
            "slide_count": len(scenes),
            "timing_mode": "content_led",
            "diagram_targets": [
                beat.get("visual_target")
                for beat in (beats or [])
                if beat.get("visual_target")
            ],
        },
    }


def _build_edit_decisions(
    scene_plan: dict[str, Any],
    duration: float,
    playbook: str,
    asset_ids: list[str],
    beats: list[dict[str, Any]] | None = None,
    narration_timeline: list[dict[str, Any]] | None = None,
    requested_duration: float | None = None,
    visual_variant: str = "balanced-grid",
    render_runtime: str = "remotion",
) -> dict[str, Any]:
    def teacher_slide_payload(beat: dict[str, Any], slide_number: int) -> dict[str, Any]:
        """Keep only the visual contract needed by the Remotion slide renderer."""
        return {
            "title": str(beat.get("title") or "Lesson point"),
            "section": str(beat.get("section") or "LESSON"),
            "bullets": [str(item) for item in (beat.get("bullets") or [])[:3]],
            "diagram": str(beat.get("diagram") or "action"),
            "keyTakeaway": str(beat.get("key_takeaway") or "Keep this idea in mind."),
            "diagramExplanation": str(beat.get("diagram_explanation") or "Read the visual from its parts to the main idea."),
            "visualTarget": str(beat.get("visual_target") or "guided diagram"),
            "slideNumber": slide_number,
            "slideCount": len(scenes),
            "visualVariant": visual_variant,
        }

    cuts = []
    scenes = scene_plan["scenes"]
    for index, scene in enumerate(scenes):
        beat = (beats[index] if beats and index < len(beats) else {})
        cuts.append({
            "id": f"cut_{index + 1}",
            "source": asset_ids[index % len(asset_ids)],
            "type": "teacher_slide",
            "teacher_slide": teacher_slide_payload(beat, index + 1),
            "backgroundColor": ILEARNZED_SLIDE_BACKGROUND,
            "surfaceColor": ILEARNZED_SLIDE_SURFACE,
            "accentColor": ILEARNZED_SLIDE_ACCENT,
            "color": ILEARNZED_SLIDE_TEXT,
            "mutedColor": ILEARNZED_SLIDE_MUTED,
            "layer": "primary",
            "in_seconds": scene["start_seconds"],
            "out_seconds": scene["end_seconds"],
            "transform": {"animation": scene["movement"]},
            "transition_in": scene["transition_in"],
            "transition_out": scene["transition_out"],
            "transition_duration": min(0.65, max(0.4, (scene["end_seconds"] - scene["start_seconds"]) / 6)),
            "reason": f"Beat {index + 1} presents {beat.get('title', 'the next lesson point')} while its narration explains the slide.",
        })
    beat_count = len(cuts)
    narration_segments = [
        {
            "asset_id": "asset_audio_narration",
            "start_seconds": segment["start_seconds"],
            "end_seconds": segment["end_seconds"],
            "offset_seconds": segment["start_seconds"],
        }
        for segment in (narration_timeline or [])
    ]
    if not narration_segments:
        narration_segments = [{"asset_id": "asset_audio_narration", "start_seconds": 0.0, "end_seconds": duration, "offset_seconds": 0.0}]
    return {
        "version": "1.0",
        "render_runtime": str(render_runtime),
        "renderer_family": "explainer-teacher",
        "composition_mode": "templated",
        "cuts": cuts,
        "audio": {"narration": {"segments": narration_segments}},
        "metadata": {
            "theme": playbook,
            "target_duration_seconds": duration,
            "requested_duration_seconds": requested_duration,
            "timing_mode": "content_led",
            "visual_beat_cadence_seconds": VISUAL_BEAT_SECONDS,
            "minimum_visual_beats": beat_count,
            "calm_words_per_minute": CALM_WORDS_PER_MINUTE,
            "slide_breath_seconds": SLIDE_BREATH_SECONDS,
            "voiceover_rate": EDGE_TTS_RATE,
            "voiceover_sync": "Each slide has a frame-addressable narration segment with a natural breathing space before the next visual beat.",
            "teaching_skill": "video-reader-ai-teacher",
            "teacher_loop": TEACHER_LOOP,
            "teaching_plan_artifact": "artifacts/teaching_plan.json",
            "teaching_cue_count": sum(len(beat.get("events", [])) for beat in (beats or [])),
            "visual_variant": visual_variant,
        },
    }


def _probe_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        return max(0.0, float(result.stdout.strip()))
    except Exception:
        return 0.0


def _teacher_tts_instructions(beat: dict[str, Any], base_instructions: str) -> str:
    """Add slide-specific delivery intent without putting visual copy in the voice."""
    objective = str(beat.get("objective") or "Explain the lesson point clearly.")
    takeaway = str(beat.get("key_takeaway") or "")
    diagram = str(beat.get("diagram_explanation") or "")
    return (
        f"{base_instructions} Slide objective: {objective} "
        f"Explain the visual relationship clearly. {diagram} "
        f"End this slide with a gentle sense of connection to the next idea. "
        f"Key takeaway to emphasize naturally: {takeaway}"
    )


def _build_tts_decision_log(
    project_id: str,
    provider: str,
    model: str,
    voice: str,
    *,
    pipeline_type: str | None = None,
    run_id: str | None = None,
    locale: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append an auditable narration-provider decision without rewriting history."""
    path = PROJECTS_DIR / project_id / "artifacts" / "decision_log.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    if not isinstance(existing, dict):
        existing = {}

    for identity_field, expected in (
        ("project_id", project_id),
        ("pipeline_type", pipeline_type),
        ("run_id", run_id),
    ):
        existing_value = existing.get(identity_field)
        if expected not in (None, "") and existing_value not in (None, "") and existing_value != expected:
            raise ValueError(
                f"existing decision log {identity_field} {existing_value!r} "
                f"does not match the current project identity {expected!r}"
            )

    decisions = list(existing.get("decisions") or [])
    voice_identity = normalize_voice_identity(
        {
            "provider": provider,
            "model": model,
            "voice_id": voice,
            "locale": locale or ("-".join(voice.split("-")[:2]) if provider in {"edge", "edge_tts"} and "-" in voice else "en-US"),
            "settings": settings or {},
        }
    )
    subject = "Narration TTS provider"
    selected_option = (
        f"openai_{model}_{voice}" if provider == "openai" else "edge_en_US_ChristopherNeural"
    ).replace("-", "_")
    prior = next(
        (
            decision
            for decision in reversed(decisions)
            if decision.get("category") == "voice_selection" and decision.get("subject") == subject
        ),
        None,
    )
    if prior and prior.get("selected") == selected_option:
        result = {
            "version": "1.0",
            "project_id": project_id,
            "voice_identity": voice_identity.contract(),
            "decisions": decisions,
        }
        if pipeline_type:
            result["pipeline_type"] = pipeline_type
        if run_id:
            result["run_id"] = run_id
        return result

    openai_option = {
        "option_id": f"openai_{model}_{voice}".replace("-", "_"),
        "label": f"OpenAI {model} / {voice}",
        "score": 1.0 if provider == "openai" else 0.72,
        "reason": "Natural conversational controls and delivery instructions for teacher-led narration.",
    }
    edge_option = {
        "option_id": "edge_en_US_ChristopherNeural",
        "label": "Edge TTS / en-US-ChristopherNeural",
        "score": 0.66 if provider == "openai" else 0.9,
        "reason": "Existing network voice path with reliable local integration.",
    }
    options = [openai_option, edge_option]
    for option in options:
        if option["option_id"] != selected_option:
            option["rejected_because"] = (
                "Not selected for this run; the configured provider/model/voice is the approved narration path."
            )
    decisions.append(
        {
            "decision_id": f"d-{len(decisions) + 1:03d}",
            "stage": "proposal",
            "category": "voice_selection",
            "subject": subject,
            "options_considered": options,
            "selected": selected_option,
            "reason": (
                f"Selected {provider} for calm, human teaching narration using {model} and {voice}."
            ),
            "user_visible": True,
            "user_approved": provider == "openai",
            "confidence": 0.94 if provider == "openai" else 0.82,
        }
    )
    result = {
        "version": "1.0",
        "project_id": project_id,
        "voice_identity": voice_identity.contract(),
        "decisions": decisions,
    }
    if pipeline_type:
        result["pipeline_type"] = pipeline_type
    if run_id:
        result["run_id"] = run_id
    return result


async def _generate_narration(
    beats: list[dict[str, Any]],
    voice: str,
    audio_dir: Path,
    planned_durations: list[float],
    *,
    tts_provider: str = "openai",
    tts_model: str = OPENAI_TTS_MODEL,
    tts_speed: float = OPENAI_TTS_SPEED,
    tts_instructions: str = OPENAI_TTS_INSTRUCTIONS,
    voice_identity: dict[str, Any] | None = None,
    cache_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Generate calm voice clips and let natural speech set each beat length.

    ``planned_durations`` is only a content-based floor.  The TTS audio is
    generated at a slightly slower rate, and a beat is extended whenever the
    natural speech needs more room.  The remaining space is a short breath so
    the next slide arrives as a human transition rather than an abrupt cut.
    """
    provider = str(tts_provider or "openai").strip().lower()
    if provider not in {"openai", "edge"}:
        raise ValueError(f"Unsupported narration provider: {provider}. Use openai or edge explicitly.")
    openai_tool = OpenAITTS() if provider == "openai" else None
    if openai_tool is not None and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OpenAI TTS is selected but OPENAI_API_KEY is not configured. "
            "Set it in the local environment or production secret store; the pipeline will not silently switch voices."
        )

    output = audio_dir / "narration.mp3"
    beats_dir = audio_dir / "beats"
    beats_dir.mkdir(parents=True, exist_ok=True)
    identity = normalize_voice_identity(
        voice_identity
        or {
            "provider": provider,
            "model": tts_model,
            "voice_id": voice,
            "locale": "-".join(voice.split("-")[:2]) if provider in {"edge", "edge_tts"} and "-" in voice else "en-US",
            "settings": {"speed": tts_speed, "instructions": tts_instructions},
        }
    )
    segment_plans = plan_narration_segments(
        [{"section_id": f"beat_{index + 1:03d}", "text": str(beat["narration"])} for index, beat in enumerate(beats)],
        identity,
        voice_rate_wpm=CALM_WORDS_PER_MINUTE,
        pause_seconds=SLIDE_BREATH_SECONDS,
    )
    segment_cache = VoiceSegmentCache(cache_dir or (audio_dir / "segments_cache"))
    normalized: list[Path] = []
    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for index, beat in enumerate(beats):
        segment_plan = segment_plans[index]
        planned = max(MIN_SLIDE_SECONDS, float(planned_durations[index]))
        raw = beats_dir / f"beat_{index + 1:03d}_raw.mp3"
        fitted = beats_dir / f"beat_{index + 1:03d}.mp3"
        cached = segment_cache.load(segment_plan)
        if cached:
            fitted = Path(str(cached.get("audio_path")))
            source_duration = float(cached.get("speech_duration_seconds") or _probe_duration(fitted) or planned)
            target = float(cached.get("timeline_duration_seconds") or max(planned, source_duration + SLIDE_BREATH_SECONDS))
        else:
            if provider == "openai":
                result = openai_tool.execute({
                    "text": str(beat["narration"]),
                    "voice": voice,
                    "model": tts_model,
                    "response_format": "mp3",
                    "instructions": _teacher_tts_instructions(beat, tts_instructions),
                    "speed": float(tts_speed),
                    "output_path": str(raw),
                })
                if not result.success:
                    raise RuntimeError(result.error or f"OpenAI TTS failed for beat {index + 1}")
            else:
                await edge_tts.Communicate(
                    str(beat["narration"]),
                    voice,
                    rate=EDGE_TTS_RATE,
                ).save(str(raw))
            if not raw.is_file() or raw.stat().st_size == 0:
                raise RuntimeError(f"{provider} TTS returned an empty narration file: {raw}")
            source_duration = _probe_duration(raw) or planned
            target = max(planned, source_duration + SLIDE_BREATH_SECONDS)
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(raw),
                    "-af", f"loudnorm=I=-16:TP=-1.5:LRA=11,apad,atrim=duration={target:.6f},asetpts=N/SR/TB",
                    "-ar", "48000", "-ac", "2", "-codec:a", "libmp3lame", "-q:a", "4", str(fitted),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            segment_cache.store(
                segment_plan,
                {
                    "audio_path": str(fitted),
                    "speech_duration_seconds": round(source_duration, 3),
                    "timeline_duration_seconds": round(target, 3),
                },
            )
        normalized.append(fitted)
        timeline.append({
            "id": f"beat_narration_{index + 1}",
            "segment_id": segment_plan["segment_id"],
            "voice_identity_key": identity.identity_key,
            "status": "cached" if cached else "completed",
            "expected_duration_seconds": segment_plan["expected_duration_seconds"],
            "measured_duration_seconds": round(target, 3),
            "start_seconds": round(cursor, 3),
            "end_seconds": round(cursor + target, 3),
            "text": beat["narration"],
            "audio_path": str(fitted),
            "speech_duration_seconds": round(source_duration, 3),
            "breath_seconds": round(max(0.0, target - source_duration), 3),
            "tts_provider": provider,
            "tts_model": tts_model if provider == "openai" else None,
            "tts_voice": voice,
            "voice_identity": identity.contract(),
            "cache_hit": bool(cached),
        })
        cursor += target

    try:
        assemble_audio_segments(normalized, output)
    except Exception as exc:
        raise RuntimeError(f"Narration assembly failed: {exc}") from exc
    return timeline


def _write_internal_demo_checkpoint(
    project_id: str,
    stage: str,
    artifacts: dict[str, Any],
    *,
    pipeline_type: str,
    run_id: str,
    **checkpoint_kwargs: Any,
) -> Path:
    """Advance one explicitly marked demo gate through immutable evidence.

    The internal runner is quarantined and never represents a production user,
    but its fixtures must still exercise the same checkpoint contract as the
    real control plane.  The synthetic approval is deliberately scoped to this
    helper and is never available to ordinary executor callers.
    """
    common = {
        "pipeline_type": pipeline_type,
        "run_id": run_id,
        **checkpoint_kwargs,
    }
    write_checkpoint(
        PROJECTS_DIR,
        project_id,
        stage,
        "awaiting_human",
        artifacts,
        **common,
    )
    pending = read_checkpoint(PROJECTS_DIR, project_id, stage)
    if pending is None or pending.get("status") != "awaiting_human":
        raise RuntimeError(f"internal demo gate did not enter awaiting_human: {stage}")
    approval = build_checkpoint_approval(
        pending,
        approver_id="internal-demo-fixture",
        decision="approve",
        notes="Synthetic approval for the explicitly marked internal demo fixture.",
    )
    return write_checkpoint(
        PROJECTS_DIR,
        project_id,
        stage,
        "completed",
        artifacts,
        timestamp=pending["timestamp"],
        approval_record=approval,
        **common,
    )


def run_project(project_id: str, *, allow_internal_demo: bool = False) -> Path:
    if not allow_internal_demo:
        raise RuntimeError(
            "lib.project_pipeline is quarantined; pass the explicit internal-demo "
            "flag only for the marked fixture"
        )
    project_dir = (PROJECTS_DIR / project_id).resolve()
    assert_internal_demo_project(project_dir)
    config_path = project_dir / "artifacts" / "project_config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"project config not found: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    title = str(config.get("title") or project_id.replace("-", " ").title())
    topic = str(config.get("topic_prompt") or config.get("topic") or "Based on facts")
    playbook = str(config.get("playbook") or "premium-minimalist")
    visual_variant = str(config.get("visual_variant") or "balanced-grid")
    legacy_voice = str(config.get("voice") or "en-US-ChristopherNeural")
    tts_provider = str(
        config.get("tts_provider")
        or os.environ.get("OPENMONTAGE_TTS_PROVIDER")
        or "openai"
    ).strip().lower()
    tts_model = str(
        config.get("tts_model")
        or os.environ.get("OPENAI_TTS_MODEL")
        or OPENAI_TTS_MODEL
    )
    tts_voice = str(
        config.get("tts_voice")
        or (legacy_voice if tts_provider == "edge" else os.environ.get("OPENAI_TTS_VOICE") or OPENAI_TTS_VOICE)
    )
    tts_speed = float(config.get("tts_speed") or os.environ.get("OPENAI_TTS_SPEED") or OPENAI_TTS_SPEED)
    tts_instructions = str(config.get("tts_instructions") or OPENAI_TTS_INSTRUCTIONS)
    voice_locale = str(config.get("tts_locale") or config.get("voice_locale") or (
        "-".join(tts_voice.split("-")[:2]) if tts_provider in {"edge", "edge_tts"} and "-" in tts_voice else "en-US"
    ))
    voice_identity = normalize_voice_identity({
        "provider": tts_provider,
        "model": tts_model,
        "voice_id": tts_voice,
        "locale": voice_locale,
        "settings": {
            "speed": tts_speed,
            "instructions": tts_instructions,
        },
    })
    voice_selection = {
        **voice_identity.contract(),
        "rationale": "Locked at proposal so every narration segment and render carries the same provider identity.",
        "sample_approval_required": bool(config.get("voice_sample_required", False)),
        "sample_approved": bool(config.get("voice_sample_approval", {}).get("approved", False)) if isinstance(config.get("voice_sample_approval"), dict) else False,
        "sample_status": "approved" if bool(config.get("voice_sample_approval", {}).get("approved", False)) else ("pending" if bool(config.get("voice_sample_required", False)) else "not_required"),
    }
    require_voice_sample_approval(
        voice_selection,
        sample=config.get("voice_sample_approval") if isinstance(config.get("voice_sample_approval"), dict) else None,
        batch=True,
    )
    # Persist the exact canonical voice contract before any narration or
    # visual asset work.  A resumed run must not silently fall back to a
    # provider/default voice because an older config omitted the identity.
    if config.get("voice_identity") != voice_identity.contract() or config.get("voice_selection") != voice_selection:
        config["voice_identity"] = voice_identity.contract()
        config["voice_selection"] = voice_selection
        _write_json(config_path, config)
    requested_duration = max(2.0, float(config.get("target_duration_seconds") or 30))
    pipeline_type = str(config.get("pipeline_type") or "animated-explainer")
    render_runtime = str(config.get("render_runtime") or "remotion").strip().lower()
    marker_payload: dict[str, Any] = {}
    try:
        marker_payload = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        marker_payload = {}
    run_id = str(config.get("run_id") or marker_payload.get("run_id") or uuid.uuid4())
    # Internal/demo projects created before durable work orders may not have a
    # run id yet.  Persist the generated identity so reruns and all downstream
    # artifacts share one stable execution id.
    if (
        config.get("project_id") != project_id
        or config.get("pipeline_type") != pipeline_type
        or config.get("run_id") != run_id
    ):
        config["project_id"] = project_id
        config["pipeline_type"] = pipeline_type
        config["run_id"] = run_id
        _write_json(config_path, config)
    # The configured duration seeds the minimum visual beat count; it is not a
    # maximum.  Speech content and the calm narration policy determine the
    # final duration below.
    beat_count = max(15, math.ceil(requested_duration / VISUAL_BEAT_SECONDS))
    template_beats = config.get("template_beats")
    beats = _build_lesson_beats(
        title,
        topic,
        beat_count,
        template_beats=template_beats if isinstance(template_beats, list) else None,
    )
    planned_durations = _estimate_beat_durations(beats)
    duration = round(sum(planned_durations), 3)

    # Backlot's create endpoint predates the canonical project marker.  Repair
    # that omission at run time so the board can identify the real pipeline and
    # render its stage rail consistently on local and server deployments.
    init_project(
        project_id,
        title=title,
        pipeline_type=pipeline_type,
        run_id=run_id,
        pipeline_dir=PROJECTS_DIR,
        style_playbook=playbook,
    )

    artifacts_dir = project_dir / "artifacts"
    audio_dir = project_dir / "assets" / "audio"
    images_dir = project_dir / "assets" / "images"
    renders_dir = project_dir / "renders"
    for directory in (artifacts_dir, audio_dir, images_dir, renders_dir):
        directory.mkdir(parents=True, exist_ok=True)

    configured_music_source = config.get("music_source")
    music_source = normalize_music_source(
        configured_music_source
        if isinstance(configured_music_source, dict)
        else {
            "source_type": "none",
            "reason": "No music source was configured for this internal/demo run.",
        }
    )
    decision_log = _build_tts_decision_log(
        project_id,
        tts_provider,
        tts_model,
        tts_voice,
        pipeline_type=pipeline_type,
        run_id=run_id,
    )
    decision_log = append_music_decision(
        decision_log,
        music_source,
        stage="proposal",
        user_approved=bool(config.get("music_source_user_approved", False)),
    )
    validate_artifact("decision_log", decision_log)
    _write_json(artifacts_dir / "decision_log.json", decision_log)
    if config.get("music_source") != music_source:
        config["music_source"] = music_source
        _write_json(config_path, config)

    emit_event(project_dir, {
        "tool": "project_pipeline",
        "event": "start",
        "stage": "pipeline",
        "project_id": project_id,
        "pipeline_type": pipeline_type,
        "run_id": run_id,
    })
    try:
        proposal_packet = {
            "version": "1.0",
            "project_id": project_id,
            "pipeline_type": pipeline_type,
            "run_id": run_id,
            "concept_options": [
                {
                    "id": "concept_a",
                    "title": title,
                    "hook": f"An insightful look into {title}.",
                    "narrative_structure": "journey",
                    "visual_approach": "Content-led educational visual beats, each held long enough for calm narration, with motion graphics and diagrams.",
                    "target_duration_seconds": duration,
                    "key_points": ["Introduction and core fundamentals", "Practical application and takeaways"],
                    "why_this_works": "A clear teaching journey gives each visual beat one job.",
                    "suggested_playbook": playbook,
                    "core_message": topic,
                    "tone": "inspiring",
                },
                {
                    "id": "concept_b",
                    "title": f"{title}: The evidence in practice",
                    "hook": f"What does {title} look like in the real world?",
                    "narrative_structure": "problem_solution",
                    "visual_approach": "Evidence-led cards, diagrams, and animated comparisons paced to the explanation.",
                    "target_duration_seconds": duration,
                    "key_points": ["The problem", "The evidence", "The practical response"],
                    "why_this_works": "A problem-to-solution structure makes the lesson actionable.",
                    "suggested_playbook": playbook,
                },
                {
                    "id": "concept_c",
                    "title": f"{title}: A quick visual guide",
                    "hook": f"Learn the essential idea behind {title} in one clear guide.",
                    "narrative_structure": "tutorial",
                    "visual_approach": "Step-by-step diagrams with localized, content-led educational pacing.",
                    "target_duration_seconds": duration,
                    "key_points": ["Define the idea", "Show how it works", "Apply it confidently"],
                    "why_this_works": "A tutorial structure supports recall and later revision.",
                    "suggested_playbook": playbook,
                },
            ],
            "selected_concept": {
                "concept_id": "concept_a",
                "rationale": "Selected for a concise, clear educational journey.",
            },
            "production_plan": {
                "pipeline": pipeline_type,
                "stages": [],
                "render_runtime": render_runtime,
                "playbook": playbook,
                "composition_mode": "templated",
                "duration_policy": "content_led",
                "requested_duration_seconds": requested_duration,
                "voice_selection": voice_selection,
                "music_source": music_source,
            },
            "cost_estimate": {
                "total_estimated_usd": 0.0,
                "line_items": [],
                "budget_verdict": "no_budget_set",
            },
            "approval": {"status": "approved"},
        }
        validate_artifact("proposal_packet", proposal_packet)
        _write_json(artifacts_dir / "proposal_packet.json", proposal_packet)
        _write_internal_demo_checkpoint(
            project_id,
            "proposal",
            {"proposal_packet": proposal_packet},
            pipeline_type=pipeline_type,
            run_id=run_id,
        )

        narration_path = audio_dir / "narration.mp3"
        narration_timeline = asyncio.run(
            _generate_narration(
                beats,
                tts_voice,
                audio_dir,
                planned_durations,
                tts_provider=tts_provider,
                tts_model=tts_model,
                tts_speed=tts_speed,
                tts_instructions=tts_instructions,
                voice_identity=voice_identity.contract(),
                cache_dir=audio_dir / "segments_cache",
            )
        )
        duration = _timeline_duration(narration_timeline)
        _materialize_teaching_events(beats, narration_timeline)
        teaching_plan = _build_teaching_plan(title, beats, narration_timeline)
        _write_json(artifacts_dir / "teaching_plan.json", teaching_plan)
        _write_json(artifacts_dir / "narration_timeline.json", {"version": "1.0", "segments": narration_timeline})
        voice_plan = {
            "version": "1.0",
            "project_id": project_id,
            "pipeline_type": pipeline_type,
            "run_id": run_id,
            "voice_identity": voice_identity.contract(),
            "sample_approval": {
                "required": voice_selection["sample_approval_required"],
                "status": voice_selection["sample_status"],
                "sample_asset_id": config.get("voice_sample_approval", {}).get("sample_asset_id") if isinstance(config.get("voice_sample_approval"), dict) else None,
                "sample_path": config.get("voice_sample_approval", {}).get("sample_path") if isinstance(config.get("voice_sample_approval"), dict) else None,
                "review_notes": config.get("voice_sample_approval", {}).get("review_notes") if isinstance(config.get("voice_sample_approval"), dict) else None,
            },
            "segments": narration_timeline,
            "transcript_verification": {"status": "not_available", "word_accuracy": None, "issues": ["STT verification is required by PR-506 before release."]},
        }
        validate_artifact("voice_plan", voice_plan)
        _write_json(artifacts_dir / "voice_plan.json", voice_plan)

        # TTS is generated before the final edit so the actual natural speech
        # duration, not a 30-second ceiling, becomes the authoritative visual
        # timeline.
        script_sections = config.get("script_sections")
        script = _build_script(
            title,
            topic,
            duration,
            script_sections=script_sections if isinstance(script_sections, list) else None,
        )
        script["voice_identity"] = voice_identity.contract()
        script["narration_segments"] = narration_timeline
        script.setdefault("voice_performance", {})
        script["voice_performance"].update({
            "sample_approval_required": voice_selection["sample_approval_required"],
            "sample_approved": voice_selection["sample_approved"] or not voice_selection["sample_approval_required"],
            "voice_identity_key": voice_identity.identity_key,
        })
        validate_artifact("script", script)
        _write_json(artifacts_dir / "script.json", script)
        _write_internal_demo_checkpoint(
            project_id,
            "script",
            {"script": script, "voice_plan": voice_plan},
            pipeline_type=pipeline_type,
            run_id=run_id,
        )

        scene_plan = _build_scene_plan(script, playbook, project_id, beats, narration_timeline)
        image_paths = _write_lesson_slides(images_dir, title, beats)
        validate_artifact("scene_plan", scene_plan)
        _write_json(artifacts_dir / "scene_plan.json", scene_plan)
        _write_internal_demo_checkpoint(
            project_id,
            "scene_plan",
            {"scene_plan": scene_plan},
            pipeline_type=pipeline_type,
            run_id=run_id,
        )

        narration_source_tool = "openai_tts" if tts_provider == "openai" else "edge_tts"
        narration_provider_metadata = {
            "provider": tts_provider,
            "voice_performance": {
                "delivery_cues_applied": tts_provider == "openai",
                "provider_text_used": False,
                "provider_settings": {
                    "model": tts_model,
                    "voice": tts_voice,
                    "speed": tts_speed,
                },
                "sample_approved": bool(voice_selection["sample_approved"] or not voice_selection["sample_approval_required"]),
                "sample_status": voice_selection["sample_status"],
                "voice_identity_key": voice_identity.identity_key,
                "provider": voice_identity.provider,
                "model": voice_identity.model,
                "voice_id": voice_identity.voice_id,
                "locale": voice_identity.locale,
                "review_notes": "Generated from the approved human-teaching delivery contract; listening review remains part of final QA.",
            },
        }
        if tts_provider == "openai":
            narration_provider_metadata["model"] = tts_model
        asset_manifest = {
            "version": "1.0",
            "voice_identity": voice_identity.contract(),
            "narration_segments": narration_timeline,
            "assets": [
                {"id": "asset_audio_narration", "type": "narration", "path": str(narration_path), "source_tool": narration_source_tool, "scene_id": "scene_1", **narration_provider_metadata},
                *[
                    {
                        "id": f"asset_audio_beat_{i}",
                        "type": "narration",
                        "path": segment["audio_path"],
                        "source_tool": narration_source_tool,
                        "scene_id": f"scene_{i}",
                        "duration_seconds": segment["end_seconds"] - segment["start_seconds"],
                        **narration_provider_metadata,
                    }
                    for i, segment in enumerate(narration_timeline, start=1)
                ],
                *[
                    {"id": f"asset_image_scene_{i}", "type": "image", "path": str(path), "source_tool": "local_svg", "scene_id": f"scene_{i}"}
                    for i, path in enumerate(image_paths, start=1)
                ],
            ],
        }
        validate_artifact("asset_manifest", asset_manifest)
        _write_json(artifacts_dir / "asset_manifest.json", asset_manifest)
        _write_internal_demo_checkpoint(
            project_id,
            "assets",
            {"asset_manifest": asset_manifest},
            pipeline_type=pipeline_type,
            run_id=run_id,
        )

        edit_decisions = _build_edit_decisions(
            scene_plan,
            duration,
            playbook,
            [f"asset_image_scene_{i}" for i in range(1, len(image_paths) + 1)],
            beats,
            narration_timeline,
            requested_duration,
            visual_variant,
            render_runtime=render_runtime,
        )
        edit_decisions.update({
            "project_id": project_id,
            "pipeline_type": pipeline_type,
            "run_id": run_id,
        })
        edit_decisions["metadata"].update({
            "project_id": project_id,
            "pipeline_type": pipeline_type,
            "run_id": run_id,
            "tts_provider": tts_provider,
            "tts_model": tts_model,
            "tts_voice": tts_voice,
            "tts_speed": tts_speed,
            "voiceover_rate": f"{tts_speed:g}x" if tts_provider == "openai" else EDGE_TTS_RATE,
        })
        validate_artifact("edit_decisions", edit_decisions)
        _write_json(artifacts_dir / "edit_decisions.json", edit_decisions)
        write_checkpoint(
            PROJECTS_DIR,
            project_id,
            "edit",
            "completed",
            {"edit_decisions": edit_decisions},
            pipeline_type=pipeline_type,
            run_id=run_id,
        )

        output_path = renders_dir / "final.mp4"
        result = VideoCompose().execute({
            "operation": "render",
            "edit_decisions": edit_decisions,
            "asset_manifest": asset_manifest,
            "audio_path": str(narration_path),
            "output_path": str(output_path),
            "proposal_packet": proposal_packet,
            "scene_plan": scene_plan,
        })
        # A renderer failure is terminal even when a previous run left a
        # canonical final.mp4 behind. Never treat file existence as proof of
        # current-run success; the render result must be successful.
        if not result.success:
            raise RuntimeError(result.error or "video composition failed")

        actual = _probe_render(output_path)
        render_report = {
            "version": "1.0",
            "project_id": project_id,
            "pipeline_type": pipeline_type,
            "run_id": run_id,
            "voice_identity": voice_identity.contract(),
            "narration_verification": {
                "segment_count": len(narration_timeline),
                "completed_segments": sum(segment.get("status") in {"completed", "cached"} for segment in narration_timeline),
                "cache_hits": sum(bool(segment.get("cache_hit")) for segment in narration_timeline),
                "transcript_matches_script": None,
                "word_accuracy": None,
                "issues": ["external transcript verification remains required before final release"],
            },
            "render_runtime": render_runtime,
            "outputs": [{
                "path": str(output_path),
                "format": "mp4",
                "codec": actual.get("codec", "h264"),
                "audio_codec": "aac",
                "resolution": actual.get("resolution", "1920x1080"),
                "fps": actual.get("fps", 30.0),
                "duration_seconds": actual.get("duration_seconds", 0.0),
                "file_size_bytes": output_path.stat().st_size,
            }],
            "render_grammar": "explainer-teacher",
            "metadata": {
                "project_id": project_id,
                "pipeline_type": pipeline_type,
                "run_id": run_id,
                "visual_beat_cadence_seconds": VISUAL_BEAT_SECONDS,
                "visual_beat_count": len(scene_plan["scenes"]),
                "timing_mode": "content_led",
                "requested_duration_seconds": requested_duration,
                "content_duration_seconds": duration,
                "calm_words_per_minute": CALM_WORDS_PER_MINUTE,
                "slide_breath_seconds": SLIDE_BREATH_SECONDS,
                "voiceover_rate": f"{tts_speed:g}x" if tts_provider == "openai" else EDGE_TTS_RATE,
                "tts_provider": tts_provider,
                "tts_model": tts_model,
                "tts_voice": tts_voice,
                "tts_speed": tts_speed,
                "visual_variant": visual_variant,
                "voiceover_sync": "Each slide is held for its natural explanation, with frame-addressable narration and a breathing space before the next visual beat.",
                "teaching_skill": "video-reader-ai-teacher",
                "teaching_cue_count": sum(len(beat.get("events", [])) for beat in beats),
            },
            "render_time_seconds": getattr(result, "duration_seconds", 0.0) or 0.0,
        }
        _write_json(artifacts_dir / "render_report.json", render_report)
        write_checkpoint(
            PROJECTS_DIR,
            project_id,
            "compose",
            "completed",
            {"render_report": render_report},
            pipeline_type=pipeline_type,
            run_id=run_id,
        )
        emit_event(project_dir, {
            "tool": "project_pipeline",
            "event": "finish",
            "stage": "pipeline",
            "success": True,
            "output_path": str(output_path),
            "project_id": project_id,
            "pipeline_type": pipeline_type,
            "run_id": run_id,
        })
        return output_path
    except Exception as exc:
        emit_event(project_dir, {
            "tool": "project_pipeline",
            "event": "error",
            "stage": "pipeline",
            "success": False,
            "error": str(exc)[:500],
            "project_id": project_id,
            "pipeline_type": pipeline_type,
            "run_id": run_id,
        })
        raise


def refresh_visuals(project_id: str, *, allow_internal_demo: bool = False) -> Path:
    """Regenerate lesson visuals and the Remotion render without rerunning TTS.

    This is the safe iteration loop for visual art direction: existing narration
    remains authoritative, while slide data, transitions, and the rendered video
    can be improved repeatedly without spending another voice-generation call.
    """
    if not allow_internal_demo:
        raise RuntimeError(
            "visual refresh through lib.project_pipeline is quarantined; use the "
            "explicit internal-demo flag only for the marked fixture"
        )
    project_dir = (PROJECTS_DIR / project_id).resolve()
    assert_internal_demo_project(project_dir)
    artifacts_dir = project_dir / "artifacts"
    audio_dir = project_dir / "assets" / "audio"
    images_dir = project_dir / "assets" / "images"
    renders_dir = project_dir / "renders"
    config_path = artifacts_dir / "project_config.json"
    timeline_path = artifacts_dir / "narration_timeline.json"
    script_path = artifacts_dir / "script.json"
    manifest_path = artifacts_dir / "asset_manifest.json"
    if not config_path.is_file() or not timeline_path.is_file() or not script_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError(
            "visual-only refresh requires project_config.json, narration_timeline.json, "
            "script.json, and asset_manifest.json"
        )

    config = json.loads(config_path.read_text(encoding="utf-8"))
    pipeline_type = str(config.get("pipeline_type") or "animated-explainer")
    render_runtime = str(config.get("render_runtime") or "remotion").strip().lower()
    marker_payload: dict[str, Any] = {}
    try:
        marker_payload = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        marker_payload = {}
    run_id = str(config.get("run_id") or marker_payload.get("run_id") or uuid.uuid4())
    if (
        config.get("project_id") != project_id
        or config.get("pipeline_type") != pipeline_type
        or config.get("run_id") != run_id
    ):
        config["project_id"] = project_id
        config["pipeline_type"] = pipeline_type
        config["run_id"] = run_id
        _write_json(config_path, config)
    init_project(
        project_id,
        title=str(config.get("title") or project_id.replace("-", " ").title()),
        pipeline_type=pipeline_type,
        run_id=run_id,
        pipeline_dir=PROJECTS_DIR,
        style_playbook=str(config.get("playbook") or "premium-minimalist"),
    )
    timeline_payload = json.loads(timeline_path.read_text(encoding="utf-8"))
    narration_timeline = timeline_payload.get("segments", [])
    if not narration_timeline:
        raise ValueError("visual-only refresh found no narration segments")
    script = json.loads(script_path.read_text(encoding="utf-8"))
    title = str(config.get("title") or project_id.replace("-", " ").title())
    topic = str(config.get("topic_prompt") or config.get("topic") or "Based on facts")
    playbook = str(config.get("playbook") or "premium-minimalist")
    visual_variant = str(config.get("visual_variant") or "balanced-grid")
    requested_duration = float(config.get("target_duration_seconds") or 30)
    duration = _timeline_duration(narration_timeline)
    template_beats = config.get("template_beats")
    beats = _build_lesson_beats(
        title,
        topic,
        len(narration_timeline),
        template_beats=template_beats if isinstance(template_beats, list) else None,
    )
    _materialize_teaching_events(beats, narration_timeline)
    teaching_plan = _build_teaching_plan(title, beats, narration_timeline)
    _write_json(artifacts_dir / "teaching_plan.json", teaching_plan)

    scene_plan = _build_scene_plan(script, playbook, project_id, beats, narration_timeline)
    image_paths = _write_lesson_slides(images_dir, title, beats)
    _write_json(artifacts_dir / "scene_plan.json", scene_plan)

    asset_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    image_by_id = {
        f"asset_image_scene_{index}": str(path)
        for index, path in enumerate(image_paths, start=1)
    }
    manifest_assets = asset_manifest.setdefault("assets", [])
    manifest_asset_ids = {asset.get("id") for asset in manifest_assets}
    for asset in asset_manifest.get("assets", []):
        if asset.get("id") in image_by_id:
            asset["path"] = image_by_id[asset["id"]]
            asset["source_tool"] = "local_svg"
    for asset_id, path in image_by_id.items():
        if asset_id not in manifest_asset_ids:
            scene_number = asset_id.rsplit("_", 1)[-1]
            manifest_assets.append({
                "id": asset_id,
                "type": "image",
                "path": path,
                "source_tool": "local_svg",
                "scene_id": f"scene_{scene_number}",
            })
    _write_json(manifest_path, asset_manifest)

    edit_decisions = _build_edit_decisions(
        scene_plan,
        duration,
        playbook,
        [f"asset_image_scene_{i}" for i in range(1, len(image_paths) + 1)],
        beats,
        narration_timeline,
        requested_duration,
        visual_variant,
        render_runtime=render_runtime,
    )
    edit_decisions.update({
        "project_id": project_id,
        "pipeline_type": pipeline_type,
        "run_id": run_id,
    })
    old_edits_path = artifacts_dir / "edit_decisions.json"
    old_edits = json.loads(old_edits_path.read_text(encoding="utf-8")) if old_edits_path.is_file() else {}
    old_metadata = old_edits.get("metadata") or {}
    edit_decisions["metadata"].update({
        "project_id": project_id,
        "pipeline_type": pipeline_type,
        "run_id": run_id,
        "tts_provider": old_metadata.get("tts_provider", config.get("tts_provider", "openai")),
        "tts_model": old_metadata.get("tts_model", config.get("tts_model", OPENAI_TTS_MODEL)),
        "tts_voice": old_metadata.get("tts_voice", config.get("tts_voice", OPENAI_TTS_VOICE)),
        "tts_speed": old_metadata.get("tts_speed", config.get("tts_speed", OPENAI_TTS_SPEED)),
        "visual_iteration": "teacher-slide-v2",
        "visual_system": "animated-ppt-grid-with-diagram-primitives",
        "visual_variant": visual_variant,
        "voiceover_reused": bool(config.get("voiceover_reused")),
        "voiceover_source_project_id": config.get("voiceover_source_project_id"),
    })
    validate_artifact("scene_plan", scene_plan)
    validate_artifact("asset_manifest", asset_manifest)
    validate_artifact("edit_decisions", edit_decisions)
    _write_json(artifacts_dir / "edit_decisions.json", edit_decisions)

    output_path = renders_dir / "final.mp4"
    proposal_packet_path = artifacts_dir / "proposal_packet.json"
    proposal_packet = json.loads(proposal_packet_path.read_text(encoding="utf-8")) if proposal_packet_path.is_file() else None
    narration_path = audio_dir / "narration.mp3"
    result = VideoCompose().execute({
        "operation": "render",
        "edit_decisions": edit_decisions,
        "asset_manifest": asset_manifest,
        "audio_path": str(narration_path),
        "output_path": str(output_path),
        "proposal_packet": proposal_packet,
        "scene_plan": scene_plan,
    })
    # A stale canonical output from an earlier attempt cannot rescue a failed
    # visual-only render.
    if not result.success:
        raise RuntimeError(result.error or "visual-only video composition failed")

    actual = _probe_render(output_path)
    render_report = {
        "version": "1.0",
        "project_id": project_id,
        "pipeline_type": pipeline_type,
        "run_id": run_id,
        "render_runtime": render_runtime,
        "outputs": [{
            "path": str(output_path),
            "format": "mp4",
            "codec": actual.get("codec", "h264"),
            "audio_codec": "aac",
            "resolution": actual.get("resolution", "1920x1080"),
            "fps": actual.get("fps", 30.0),
            "duration_seconds": actual.get("duration_seconds", 0.0),
            "file_size_bytes": output_path.stat().st_size,
        }],
        "render_grammar": "explainer-teacher",
        "metadata": {
            "project_id": project_id,
            "pipeline_type": pipeline_type,
            "run_id": run_id,
            "visual_beat_cadence_seconds": VISUAL_BEAT_SECONDS,
            "visual_beat_count": len(scene_plan["scenes"]),
            "timing_mode": "content_led",
            "requested_duration_seconds": requested_duration,
            "content_duration_seconds": duration,
            "calm_words_per_minute": CALM_WORDS_PER_MINUTE,
            "slide_breath_seconds": SLIDE_BREATH_SECONDS,
            "tts_provider": edit_decisions["metadata"]["tts_provider"],
            "tts_model": edit_decisions["metadata"]["tts_model"],
            "tts_voice": edit_decisions["metadata"]["tts_voice"],
            "tts_speed": edit_decisions["metadata"]["tts_speed"],
            "voiceover_sync": "Narration was preserved; each visual beat now uses animated slide primitives and an overlapped transition window.",
            "teaching_skill": "video-reader-ai-teacher",
            "visual_system": "animated-ppt-grid-with-diagram-primitives",
            "visual_variant": visual_variant,
            "voiceover_reused": bool(config.get("voiceover_reused")),
            "voiceover_source_project_id": config.get("voiceover_source_project_id"),
            "teaching_cue_count": sum(len(beat.get("events", [])) for beat in beats),
        },
        "render_time_seconds": getattr(result, "duration_seconds", 0.0) or 0.0,
    }
    _write_json(artifacts_dir / "render_report.json", render_report)
    write_checkpoint(
        PROJECTS_DIR,
        project_id,
        "edit",
        "completed",
        {"edit_decisions": edit_decisions},
        pipeline_type=pipeline_type,
        run_id=run_id,
    )
    write_checkpoint(
        PROJECTS_DIR,
        project_id,
        "compose",
        "completed",
        {"render_report": render_report},
        pipeline_type=pipeline_type,
        run_id=run_id,
    )
    emit_event(project_dir, {
        "tool": "project_pipeline",
        "event": "visual_refresh_finish",
        "stage": "pipeline",
        "success": True,
        "output_path": str(output_path),
        "project_id": project_id,
        "pipeline_type": pipeline_type,
        "run_id": run_id,
    })
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a complete OpenMontage Backlot project")
    parser.add_argument("project_id")
    parser.add_argument("--visual-only", action="store_true", help="Refresh slide visuals/transitions while reusing existing narration")
    parser.add_argument(
        "--internal-demo",
        action="store_true",
        help="Allow the explicitly marked internal demo fixture (never a production path)",
    )
    args = parser.parse_args()
    print(f"Starting OpenMontage project pipeline: {args.project_id}{' (visual-only)' if args.visual_only else ''}")
    output = (
        refresh_visuals(args.project_id, allow_internal_demo=args.internal_demo)
        if args.visual_only
        else run_project(args.project_id, allow_internal_demo=args.internal_demo)
    )
    print(f"Pipeline execution complete! Deliverable at: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
