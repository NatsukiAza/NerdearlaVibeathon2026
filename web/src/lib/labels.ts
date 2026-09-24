/** Shared language labels — names in their own language (ADR-0011). */
import type { TrackLang } from "../lib/types";

export const TRACK_OPTIONS: { lang: TrackLang; label: string }[] = [
  { lang: "original", label: "Original" },
  { lang: "es", label: "Español" },
  { lang: "en", label: "English" },
  { lang: "pt", label: "Português" },
];

export function trackLabel(lang: TrackLang): string {
  return TRACK_OPTIONS.find((o) => o.lang === lang)?.label ?? lang;
}

export const PRODUCTION_TOKEN_KEY = "nerdearla.productionToken";
