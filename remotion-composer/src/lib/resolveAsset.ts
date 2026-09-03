import { staticFile } from "remotion";

/** Resolve remote, absolute local, and public/relative media paths. */
export function resolveAsset(src: string): string {
  if (
    src.startsWith("http://") ||
    src.startsWith("https://") ||
    src.startsWith("data:")
  ) {
    return src;
  }

  const clean = src.replace(/^file:\/\/\/?/, "");
  if (clean.startsWith("/") || /^[A-Za-z]:[\\/]/.test(clean)) {
    const posix = clean.replace(/\\/g, "/");
    return posix.startsWith("/") ? `file://${posix}` : `file:///${posix}`;
  }

  return staticFile(clean);
}
