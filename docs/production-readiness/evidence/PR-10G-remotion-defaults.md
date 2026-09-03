# PR-10G — Remotion default-props smoke

Status: **PASS locally; supported container proof pending**

## Findings and fixes

The default composition sweep found four classes of avoidable cancellation:

- `ProductReveal` declared `airnothing/product.png`, which is not shipped in
  `remotion-composer/public/`.
- `TalkingHead`, `TitledVideo`, and `LyricOverlay` passed an empty `videoSrc`
  to `staticFile("")`, resolving to the non-existent `/public/` directory.
- `SignalFromTomorrowWithMusic` loaded a rich video/audio fixture whose demo
  media is intentionally not shipped in `public/`.

The defaults now use explicit assetless previews: ProductReveal shows a
deterministic letter-card placeholder, the video-led compositions render a
black canvas, and Signal uses a title-only preview fixture. A non-empty image
or video path remains strict and is never silently replaced.

## Verification

| Check | Result |
|---|---|
| `npx --no-install tsc --noEmit -p tsconfig.json` | **PASS** |
| `npx --no-install remotion still src/index.tsx ProductReveal ... --frame=0` | **PASS**, non-empty PNG |
| `npx --no-install remotion still src/index.tsx ProductReveal ... --frame=120` | **PASS**, placeholder and text rendered; non-empty PNG visually inspected |
| `SignalFromTomorrowWithMusic`, `TalkingHead`, `TitledVideo`, and `LyricOverlay` default stills | **PASS**, all non-empty PNGs after the empty-source/preview fix |
| Full 13-composition default still sweep | **PASS locally** — [`PR-10G-remotion-default-sweep.json`](PR-10G-remotion-default-sweep.json) |
| CI container smoke | **Added**; renders six default/assetless compositions with the baked browser and retains `openmontage-container-render` |

The local stills used the installed system Chrome because this workstation
cannot download the disposable Remotion browser. Ubuntu CI remains the
authoritative clean-browser/container proof.
