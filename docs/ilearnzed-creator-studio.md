# iLearnZed Creator Studio Profile

This repository is configured for iLearnZed's learner-first YouTube strategy.
The machine-readable source of truth is `profiles/ilearnzed.yaml`; the visual
system is `styles/ilearnzed-education.yaml`.

## Template library

The callable templates live under `content_templates/` and are loaded through
`lib/content_templates.py`:

- `ilearnzed-exam-lesson.yaml` — teacher-led past papers, worked examples, and exam technique.
- `ilearnzed-concept-explainer.yaml` — misconception-led visual explanations.
- `ilearnzed-study-methods.yaml` — demonstrated study and examination routines.
- `ilearnzed-teacher-onboarding.yaml` — verified educator workflows and course creation.
- `ilearnzed-product-demonstration.yaml` — evidence-led learner, teacher, or institution workflows.
- `ilearnzed-shorts.yaml` — self-contained vertical lessons and long-form derivatives.

Each template points to an existing OpenMontage pipeline and requires a
validated thumbnail package, teaching structure, consent evidence, and final
review before publication.

## Operating brief

- Goal: reach secondary learners through useful exam-preparation content and
  convert interested viewers into registrations.
- Learner range: Form 1 through Form 6.
- Subject order: Mathematics, Biology, Chemistry, Physics, then English.
- Default format: teacher-led. Presenter-led and faceless animation support
  concepts that benefit from demonstration or visual explanation.
- Cadence: one long-form video and three to five Shorts each week initially;
  consider two long-form videos only after quality and review are stable.
- Long-form duration: use 300 seconds as a planning seed when helpful; final
  duration follows the teaching content and narration using the
  `ilearnzed_long_form` 1920×1080 profile. The studio does not impose a
  10-minute maximum. Assemble purposeful teacher, stock, generated, Remotion,
  HyperFrames, and FFmpeg segments; do not stretch a single generated clip to
  reach a target.
- Visual pacing: use at least one purposeful editorial beat every two seconds
  as the minimum authored cadence (15 beats in 30 seconds, 30 beats in 60
  seconds, and proportionally more for longer lessons). This is not a maximum
  duration rule: a narrated slide may remain on screen longer when the content
  needs it. A beat may be a new image, a diagram reveal, a camera move, a
  teacher insert, or a meaningful text change. The renderer validates
  continuous coverage, explicit content-led beat counts, and bounded transition
  durations before rendering.
- Lesson-slide rule: when a beat teaches a concept, its visual source should be
  a distinct PPT-like lesson slide with a short title, up to three teaching
  points, and a diagram or worked visual suited to that beat. Keep the
  iLearnZed frame, typography, and dark-green identity consistent while the
  content, diagram, and emphasis change from slide to slide. Do not satisfy
  the beat count by recolouring one background image.
- Motion and narration: every beat receives deterministic entry/exit handling;
  stills support zoom, pan, Ken Burns, float, pulse, and draw-on reveals while
  diagrams can remain visible and animate as narration continues. The default
  runner writes one narration clip per lesson slide at a calm speech rate,
  preserves natural speech speed, holds each slide for the content, and adds a
  short breathing space before the next visual. The clips are also concatenated
  into a frame-addressable voice track. Narration must not overlap or leave
  unplanned gaps.
- Diagram-engineering standard: all future lesson slides use the reusable
  semantic diagram workflow: choose the visual type from the meaning, calculate
  geometry from safe-area tokens, route connectors to node boundaries, preserve
  high-contrast typography, and render-check the actual output at delivery
  resolution. The slide frame stays brand-consistent while wording, data,
  diagram type, and emphasis are authored per beat.
- Language: English canonical version, with selective Bemba and Nyanja
  adaptations beginning with Mathematics.
- Default CTA: `Join iLearnZed and start learning` → `https://ilearnzed.org`.

## Production contract

Every proposed video should record:

1. the learner and their problem;
2. the verified curriculum or exam context;
3. the central question or misconception;
4. five title options and two or three thumbnail variants;
5. the visual-first opening and promised payoff;
6. the worked example, diagram, or teacher demonstration;
7. the registration or verified subject-specific CTA;
8. the Shorts derivatives and their independent payoffs;
9. consent evidence for every identifiable teacher or student;
10. the single post-publication experiment to review.

## Runtime portability

The studio is local-first but server-portable. Python tools read provider
credentials from environment variables, Remotion uses local/system font
fallbacks, and Backlot binds to localhost by default. For a server or container,
set `BACKLOT_HOST=0.0.0.0`, persist `projects/` and `output/`, and provide
provider credentials through the server's secret store. The repository's
`Dockerfile` and `docker-compose.yml` provide the repeatable runtime baseline.

## Thumbnail rules

Thumbnails must communicate at a glance without depending on the title. Use
one focal subject, a short truthful phrase, and visible proof of the lesson's
value: a worked question, diagram, teacher expression, or result. Keep the
existing iLearnZed dark-green/technology identity and use the approved logo
asset with padding in the bottom-right. Do not introduce a new font or palette
without approval.

## Preview workflow

After `thumbnail_package` is created, run the local
`thumbnail_preview_renderer` with the Backlot project directory. It writes
three project-local SVG previews under `assets/images/thumbnails/`, embeds the
approved iLearnZed logo when available, and updates the package with
`preview_path` values. Backlot then shows the previews in the packaging review
surface. These are review assets, not a substitute for a final photographic or
generated artwork pass when the selected concept needs one.

## Safeguarding

Teachers may be recorded only after written or digitally recorded consent that
covers YouTube, Shorts, social repurposing, thumbnails, promotional clips,
and iLearnZed platform use. Identifiable students, especially minors, require
the appropriate consent process and are excluded by default.
