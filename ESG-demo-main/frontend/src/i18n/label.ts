import type { Lang } from "./dict";

export function pickLabel(
  item: { label_zh?: string; label_en?: string; label?: string },
  lang: Lang
) {
  if (lang === "zh") return item.label_zh || item.label || item.label_en || "";
  return item.label_en || item.label || item.label_zh || "";
}
