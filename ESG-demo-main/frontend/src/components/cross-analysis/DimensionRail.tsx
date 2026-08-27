"use client";

import React from "react";
import { useT } from "@/i18n/useT";
import type { CrossDimension } from "@/data/crossTaxonomy";

export default function DimensionRail({
  dimensions,
  activeKey,
  onSelect,
}: {
  dimensions: CrossDimension[];
  activeKey: string;
  onSelect: (key: string) => void;
}) {
  const { t, lang } = useT();
  return (
    <div>
      <div className="mb-3 text-xs font-medium text-slate-500">{t("crossAnalysis.dimensions")}</div>
      <div className="flex flex-col gap-1">
        {dimensions.map((d) => {
          const active = d.key === activeKey;
          return (
            <button
              key={d.key}
              onClick={() => onSelect(d.key)}
              className={
                "group w-full rounded-xl px-3 py-2 text-left transition " +
                (active
                  ? "bg-slate-900 text-white shadow-sm"
                  : "hover:bg-white/70 text-slate-700")
              }
            >
              <div className="text-sm font-semibold tracking-tight">{lang === "zh" ? d.label_zh : d.label_en}</div>
              <div className={"mt-1 text-xs leading-snug " + (active ? "text-white/80" : "text-slate-500")}
              >
                {d.intro}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
