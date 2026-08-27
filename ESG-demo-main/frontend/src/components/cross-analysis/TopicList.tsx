"use client";

import React from "react";
import type { CrossDimension } from "@/data/crossTaxonomy";

export default function TopicList({
  dimension,
  activeTopicKey,
  onSelect,
}: {
  dimension: CrossDimension;
  activeTopicKey: string;
  onSelect: (topicKey: string) => void;
}) {
  return (
    <div>
      <div className="flex flex-col gap-1">
        {dimension.issues.map((t) => {
          const active = t.key === activeTopicKey;
          return (
            <button
              key={t.key}
              onClick={() => onSelect(t.key)}
              className={
                "w-full rounded-xl px-3 py-2 text-left transition " +
                (active
                  ? "bg-white text-slate-900 shadow-sm border border-slate-200"
                  : "hover:bg-white/70 text-slate-700")
              }
            >
              <div className="flex items-baseline justify-between gap-2">
                <div className="text-sm font-semibold tracking-tight">{t.label_en}</div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
