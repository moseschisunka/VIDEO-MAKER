# African Trending Educational Video Strategist (Zambia + Pan-African)

> Sources: ECZ (Examinations Council of Zambia) syllabus structure, KNEC (Kenya) / WAEC (West
> Africa) / CAPS (South Africa) curricula as comparative reference, GSMA "Mobile Economy
> Sub-Saharan Africa" reports (device/data context), We Are Social / Meltwater "Digital" country
> reports for Africa (platform-share context), YouTube/Instagram/Facebook creator documentation.
> Builds on `skills/creative/short-form.md`, `skills/creative/long-form.md`, and
> `skills/creative/storytelling.md` — read those for the underlying pacing/retention mechanics;
> this skill adds the regional layer on top.

This skill turns the agent into a content strategist and visual director for **trending
educational ("edutainment") video aimed at Zambian and broader African audiences**, across
YouTube, Instagram, and Facebook simultaneously. It covers two things generic short-form/
long-form skills don't: how to break a topic down so it is both curriculum-grounded and
genuinely shareable, and how to art-direct it for the real device/bandwidth/platform-mix
conditions of the region — not a copy-paste of Western TikTok playbooks.

## Regional Context That Shapes Every Decision

| Factor | What it means for production |
|---|---|
| **Facebook is not secondary here** | Facebook (feed video, Watch, subject/exam-prep Groups) is a primary distribution channel across much of Sub-Saharan Africa, not just a cross-post afterthought. Never skip a Facebook-native cut. |
| **Mobile-first, data-constrained** | Assume mid-range Android phones and metered data. Keep exports efficient (H.264, sensible bitrate — see `skills/creative/short-form.md` upload specs) rather than defaulting to the heaviest possible encode. A video that "costs too much data to finish" gets abandoned regardless of quality. |
| **Sound-off is the default, not the exception** | Public transport, shared data, classrooms, low-privacy home settings — assume muted playback more aggressively than a Western short-form estimate. Captions and bold on-screen text are load-bearing, not decoration. |
| **Aspect ratio is platform-mixed, not vertical-only** | Vertical 9:16 wins reach on Reels/Shorts, but Facebook feed still carries meaningful 1:1 and 4:5 traffic from an older/broader demographic. Plan a square or 4:5 Facebook-feed crop alongside the vertical cut — don't just upload the vertical file to all three. |
| **Multilingual code-switching** | English is the exam/official language, but hooks and captions land harder with a local-language beat layered in (Zambia: Bemba, Nyanja, Tonga, Lozi; adapt per target country). English narration + one code-switched line in the hook or caption is a proven pattern — don't force full local-language narration unless the brief asks for it. |
| **Curriculum grounding = trend durability** | Generic "explainer" topics compete with the entire internet. Topics anchored to a real syllabus (ECZ for Zambia; KNEC/WAEC/CAPS elsewhere) compete in a much smaller, higher-intent pool: students actively searching for exam help. This is what makes educational content "trending" *and* durable, not just novel. |
| **Trends propagate regionally first** | A sound or format trending on Facebook/Instagram in Nigeria or South Africa often reaches Zambia before it reaches from the US/EU. When doing trend research, check regional creators and regional Reels/Watch activity before defaulting to global trend lists. |
| **School calendar drives seasonality** | Zambia's academic year runs three terms, with exam pressure concentrated toward each term's end and heaviest around Oct-Dec (Grade 9/12 national exams). Batch exam-prep series to land ahead of these windows, not evergreen-only. |

## Topic Breakdown Methodology

Do not free-associate topics. Run every brief through this sequence during the `research` and
`proposal` stages:

1. **Anchor to a real syllabus point.** Pull the specific ECZ (or relevant national) syllabus
   topic, grade level, and — where possible — the kind of question students historically get
   wrong on it. A civics/health/life-skills topic can anchor to a national curriculum's
   life-skills or civic-education strand instead.
2. **Cross the syllabus point with a hook lens.** Pick one:
   - **Misconception-first** — "Most students think X. Here's why that's wrong." (pairs with the
     misconception-first research finding in `storytelling.md`)
   - **Exam-trap** — "This is the #1 mistake markers see on this question."
   - **Local-application** — connect the abstract concept to something concretely Zambian/African
     (mobile money and percentages, maize yield and ratios, malaria transmission and biology,
     load-shedding and basic circuits).
   - **Myth-vs-reality** — best for health/civic/science topics with real local misinformation.
   - **Cost-of-not-knowing** — "Skip this and you'll lose 2 marks every year" for exam content.
3. **Score before committing.** Rate each candidate topic 1-5 on: curriculum relevance, how often
   it trips students up (exam-frequency proxy), visual explainability (can this be *shown*, not
   just told?), local relatability, and evergreen-vs-seasonal fit. Reject low scorers rather than
   defaulting to the first idea.
4. **Produce 3 differentiated angles per topic** — the same discipline OpenMontage already uses
   for reference-driven concepts (`skills/meta/video-reference-analyst.md`). E.g. for "the mole
   concept": (a) misconception-first chemistry explainer, (b) exam-trap walkthrough of a past ECZ
   question, (c) local-application angle using a market/cooking-ratio analogy. Present all three
   at the proposal gate; let the user pick, don't silently choose one.

## Platform-Specific Visual Direction

| Platform | Primary format | Duration | Crop | Notes for this region |
|---|---|---|---|---|
| **YouTube** | Long-form exam-prep + Shorts as funnel | 6-12 min long-form; 15-45s Shorts | 16:9 long-form; 9:16 Shorts | Chapters are critical for long-form — students scrub to their specific sub-topic. Shorts should tease one exam-trap or misconception and drive to the full video via end-card, not stand alone. |
| **Instagram** | Reels primary, Stories for daily micro-lessons, feed carousel reusing caption text as a study-card post | 15-45s Reels | 9:16 Reels; 1:1 or 4:5 feed carousel | Carousels (swipeable "study card" posts built from the script's key beats) extend the life of one video into 3-5 extra posts at near-zero extra cost — plan this at the `publish` stage. |
| **Facebook** | Native upload video (not a shared link), Watch, subject-specific Groups | 30-90s feed video; longer native uploads perform fine if hook holds | 1:1 or 4:5 for feed; 9:16 for Facebook Reels | Native uploads outperform cross-posted/linked video in feed ranking. Burn in captions unconditionally — assume sound-off by default here more than any other platform. Sharing into subject/exam-prep Groups is a real distribution channel; design the closing CTA to be shareable ("send this to a friend before the exam"), not just "like and subscribe." |

Across all three: **caption everything, in-frame, burned in** — don't rely on platform
auto-captions, which handle Zambian English accents and code-switched phrases poorly. Use
`subtitle_gen` with word-level timing per `skills/creative/short-form.md`.

## Visual Style & Production Mapping

- **Style playbook choice:** `clean-professional` or `flat-motion-graphics` for most exam-prep
  content (trustworthy, legible, fast to produce in volume); `minimalist-diagram` for
  math/science concepts that need a labeled diagram over a talking point; the `ink-sketch` /
  Ink Theater engine (`skills/creative/ink-theater.md`) works well for younger-audience or
  life-skills content that wants a warmer, hand-drawn feel.
- **Voice:** `edge_tts` (Microsoft neural voices) for clear, energetic exam-prep narration;
  `piper_tts` where a fully offline/free path matters. Either way, keep pacing close to
  150-160 wpm (per `storytelling.md`) — faster than that reads as rushed to an audience often
  processing content in a second or third language.
- **Representation matters — check every generated visual.** If image/video generation is used,
  explicitly steer prompts toward the actual local context (schools, markets, clothing,
  geography, skin tones representative of the target audience) rather than accepting a
  default/generic-Western output. This is a production quality bar, not an optional nicety —
  unrepresentative visuals undermine trust with the target audience immediately. Flag this
  explicitly at the `assets` stage review.
- **Thumbnail / cover-frame rule:** bold expressive face (if avatar/presenter) or a bold labeled
  diagram + large text naming the exact curriculum point, e.g. *"ECZ Grade 12 Chemistry: The Mole
  Concept in 90 Seconds"* — specific beats generic every time for this audience, because viewers
  are actively searching for their exact topic, not browsing for entertainment.

## Structure Templates

**Exam-Trap (best default for curriculum content)**
```
[0-2s]   HOOK: State the common wrong answer or mistake, on screen, bluntly.
[2-8s]   WHY IT'S WRONG: One sentence, one visual.
[8-N-15s] THE CORRECT CONCEPT: Build it step by step, one idea per beat (Mayer's Segmenting).
[N-15..N-5s] WORKED EXAMPLE: Apply it to a real/past-paper-style question.
[N-5..N] RECAP + CTA: One-sentence recap; "follow for the next exam trap" / "share with a friend before the exam."
```

**Myth vs Reality** (health/civic/science)
```
[0-2s]   HOOK: State the myth as fact, deadpan.
[2-6s]   TWIST: "Actually..." — reveal it's false.
[6-N-10s] THE REAL MECHANISM: explain what's actually true and why the myth persists.
[N-10..N] LOCAL RELEVANCE + CTA: connect to real local stakes; prompt share/comment.
```

**Local-Application**
```
[0-3s]   HOOK: Familiar local scenario (market, mobile money, farm, weather).
[3-10s]  THE HIDDEN CONCEPT: name the curriculum concept inside the familiar scenario.
[10-N-10s] BUILD: teach the concept using the local scenario as the running example throughout.
[N-10..N] BRIDGE BACK TO EXAM: "and that's exactly what Grade 12 Paper 1 tests."
```

## Cadence & Series Thinking

Single videos rarely build an audience; series do. Recommend:

- **Recurring host/voice/visual identity** across a topic series so it's recognizable in-feed —
  lock the voice and style playbook choice once per series, not per video.
- **Batch production via the `clip-factory` pipeline** when repurposing one long lesson into
  platform-specific shorts (YouTube long-form -> Shorts + Reels + Facebook cuts from the same
  source).
- **Align release cadence to the school-term calendar** — ramp exam-trap and past-paper content
  in the weeks before each term's exam window rather than spreading evenly year-round.

## Brand — iLearnZed (Default Client for This Project)

Unless the user says otherwise, every video produced under this skill is for **iLearnZed** — a
pan-African learning platform (ilearnzed.org) helping secondary school students pass exams and
build practical/career skills, teaching to local curriculum. This is binding, not optional:

1. **Attribution is mandatory.** Every video must credit iLearnZed — via an intro/outro brand
   card, on-screen lower-third, or narration mention, and in the `publish` stage's metadata/
   description (include `ilearnzed.org`).
2. **Every video ends with a CTA beat.** Follow, subscribe, like, and comment prompts, plus an
   explicit engagement question asking viewers **where they're watching from** — this leans into
   iLearnZed's pan-African, multi-country audience. Write this as a closing beat in every script,
   not an afterthought bolted on at publish.
3. Apply this by default at the `script` and `scene_plan` stages — don't wait for the user to
   request the outro/attribution beat per video.

## Applying to OpenMontage

1. **Pipeline choice:** `animated-explainer` for a single topic-deep video; `clip-factory` when
   repurposing one lesson into a YouTube-long + Shorts + Reels + Facebook batch from a single
   source recording or script.
2. **Research stage:** pull the specific syllabus point (ECZ or relevant national curriculum) and
   at least one regional trend signal (not just global) before writing the brief.
3. **Proposal stage:** present all 3 angle variants from the Topic Breakdown Methodology, plus the
   platform cut plan (YouTube long + Shorts / Reels / Facebook feed crop), voice choice
   (`edge_tts` vs `piper_tts`), and style playbook — per the existing Decision Communication
   Contract in `AGENT_GUIDE.md`, don't silently pick one.
4. **Scene plan stage:** apply the representation check above to every visual; use
   `minimalist-diagram` conventions for any math/science labeling.
5. **Assets stage:** caption-first — generate `subtitle_gen` word-level captions for every cut,
   burned in, regardless of platform.
6. **Publish stage:** package the platform-specific exports (16:9 + 9:16 for YouTube, 9:16 Reels +
   1:1/4:5 carousel captions for Instagram, 1:1/4:5 + native-upload note for Facebook), and write
   a share-oriented CTA line ("send this to a friend before the exam") into the publish metadata,
   not just a generic subscribe prompt.
