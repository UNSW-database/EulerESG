"use client";

import React from "react";
import { useT } from "@/i18n/useT";
import { Skeleton } from "antd";
import type { CrossCompareResponse } from "@/lib/api";

export default function InsightPanel({
  loading,
  compare,
}: {
  loading: boolean;
  compare: CrossCompareResponse | null;
}) {
  const { t } = useT();
  if (loading) {
    return <Skeleton active paragraph={{ rows: 3 }} />;
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/60 p-4 shadow-sm backdrop-blur">
      <div className="text-sm font-semibold text-slate-900">{t("crossAnalysis.insight.title")}</div>
      <div className="mt-1 text-xs text-slate-500">
        {t("crossAnalysis.insight.subtitle")}
      </div>
      <div className="mt-3 text-sm leading-relaxed text-slate-700">
        {compare?.insight || t("crossAnalysis.insight.empty")}
      </div>
    </div>
  );
}
