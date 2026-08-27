"use client";

import { useMemo } from "react";
import { useT } from "@/i18n/useT";

interface CompanyLegendItem {
  label: string;
  color: string;
}

interface NewHeaderProps {
  title?: string;
  dimension?: string;
  reports?: string[];
  frameworkLabel?: string | null;
  semiIndustryLabel?: string | null;
  companyLegend?: CompanyLegendItem[];
}

export function NewHeader({
  title,
  dimension,
  reports = [],
  frameworkLabel,
  semiIndustryLabel,
  companyLegend = [],
}: NewHeaderProps) {
  const { t } = useT();

  const contextLabel = useMemo(() => {
    if (frameworkLabel && semiIndustryLabel) return `${frameworkLabel} · ${semiIndustryLabel}`;
    if (frameworkLabel) return String(frameworkLabel);
    if (semiIndustryLabel) return String(semiIndustryLabel);
    return null;
  }, [frameworkLabel, semiIndustryLabel]);

  const legendItems = companyLegend.length
    ? companyLegend
    : reports.map((label, index) => ({
        label,
        color: ["#1677ff", "#52c41a", "#faad14", "#f5222d", "#722ed1", "#13c2c2", "#eb2f96", "#a0d911"][index % 8],
      }));

  return (
    <div className="bg-white rounded-2xl shadow-sm px-6 py-5 w-full">
      <div className="flex flex-col gap-4.5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2.5 min-w-0">
          <h1 className="text-[29px] md:text-[31px] font-bold text-[#0F172A] leading-none">
            {title || t("crossAnalysis.title")}
          </h1>
          {contextLabel ? (
            <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-3.5 py-1 text-sm font-medium text-slate-600">
              {contextLabel}
            </span>
          ) : null}
        </div>

        {dimension ? <p className="text-sm text-[#64748B]">{dimension}</p> : null}

        {legendItems.length ? (
          <div className="flex flex-wrap items-center gap-x-8 gap-y-3 pt-1">
            {legendItems.map((item) => (
              <div key={item.label} className="inline-flex items-center gap-2.5 min-w-0 pr-2">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-full shrink-0"
                  style={{ backgroundColor: item.color }}
                  aria-hidden
                />
                <span className="text-[15px] font-medium text-[#334155] truncate">{item.label}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
