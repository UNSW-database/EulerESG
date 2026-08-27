"use client";

import { ChevronRight } from "lucide-react";
import { useMemo, useState, useCallback } from "react";
import { useT } from "@/i18n/useT";

function secondaryKey(primary: string, secondary: string): string {
  return `${primary}|${secondary}`;
}

const CATEGORY_LABELS_NO_SECONDARY = new Set([
  "Quantitative",
  "Qualitative",
  "Discussion and Analysis",
  "General",
]);

export type TertiaryMap = Map<string, Map<string, string[]>>;
export type DirectNavigationSelection =
  | { level: "primary"; primary: string }
  | { level: "secondary"; primary: string; secondary: string }
  | {
      level: "tertiary";
      primary: string;
      secondary: string;
      metricName: string;
    };

export interface NewSidebarProps {
  primaryOptions: string[];
  secondaryByPrimary: Map<string, string[]>;
  tertiaryByPrimaryAndSecondary: TertiaryMap;
  selectedPrimary: string;
  /** Only the option directly selected by the user receives blue styling. */
  directSelection?: DirectNavigationSelection | null;
  selectedSecondaries: string[];
  selectedTertiary: string | null;
  expandedPrimaries: Record<string, boolean>;
  primaryIsActivityMetrics?: boolean;
  forceSecondaryLeafMode?: boolean;
  /** Render as a nested section of the global dashboard sidebar. */
  embedded?: boolean;
  /** @deprecated Disclosure completeness is now opened from DashboardSidebar. */
  viewMode?: "issue" | "disclosure";
  onTogglePrimary: (primary: string) => void;
  onSelectSecondary: (primary: string, secondary: string) => void;
  onSelectTertiary: (primary: string, secondary: string, metricName: string) => void;
  /** @deprecated Disclosure completeness is now opened from DashboardSidebar. */
  onSelectDisclosure?: () => void;
}

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

export function NewSidebar({
  primaryOptions,
  secondaryByPrimary,
  tertiaryByPrimaryAndSecondary,
  selectedPrimary,
  directSelection = null,
  selectedSecondaries,
  selectedTertiary,
  expandedPrimaries,
  primaryIsActivityMetrics = false,
  forceSecondaryLeafMode = false,
  embedded = false,
  onTogglePrimary,
  onSelectSecondary,
  onSelectTertiary,
}: NewSidebarProps) {
  const { t } = useT();
  const selectedSecondarySet = useMemo(() => new Set(selectedSecondaries || []), [selectedSecondaries]);
  const [expandedSecondaries, setExpandedSecondaries] = useState<Record<string, boolean>>({});
  const isActivityMetrics = primaryIsActivityMetrics && safeTrim(selectedPrimary) === "Activity Metrics";
  const secondaryActsAsLeaf = isActivityMetrics || forceSecondaryLeafMode;

  const toggleSecondaryExpand = useCallback((primary: string, secondary: string) => {
    const key = secondaryKey(primary, secondary);
    setExpandedSecondaries((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  return (
    <div className={embedded ? "min-w-0 py-1 pl-5" : "w-[320px] bg-white rounded-2xl shadow-sm p-4 h-fit"}>
      {!embedded ? (
        <h3 className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-4 pl-8">
          {t("crossAnalysis.navigation")}
        </h3>
      ) : null}

      <div className="space-y-1">
        {primaryOptions.map((primary) => {
          const isExpanded = !!expandedPrimaries?.[primary];
          const isActivePrimary =
            directSelection?.level === "primary" &&
            directSelection.primary === primary &&
            safeTrim(selectedPrimary) === primary;
          const rawSecondaries = secondaryByPrimary.get(primary) || [];
          const isActivityMetricsPrimary = primary === "Activity Metrics";
          const secondaries =
            isActivityMetricsPrimary
              ? rawSecondaries.filter((s) => !CATEGORY_LABELS_NO_SECONDARY.has(s))
              : rawSecondaries;
          const innerTertiary = tertiaryByPrimaryAndSecondary.get(primary);

          return (
            <div key={primary}>
              <button
                onClick={() => onTogglePrimary(primary)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-xl transition-[color,background-color,border-color,box-shadow] duration-150 ease-[var(--motion-fluid)] ${
                  isActivePrimary
                    ? "bg-[#EFF6FF] border border-[#BFDBFE] text-[#0F172A]"
                    : "bg-transparent border border-transparent text-[#0F172A] hover:bg-slate-50"
                }`}
              >
                <span className="text-sm font-medium truncate">{primary}</span>
                {/* <ChevronRight
                  className={`w-4 h-4 text-[#64748B] transition-transform ${
                    isExpanded ? "rotate-90" : ""
                  }`}
                /> */}
              </button>

              {isExpanded && (
                <div className="mt-1 ml-3 space-y-1">
                  {secondaries.length ? (
                    secondaries.map((secondary) => {
                      const isSelectedSecondary =
                        directSelection?.level === "secondary" &&
                        directSelection.primary === primary &&
                        directSelection.secondary === secondary &&
                        safeTrim(selectedPrimary) === primary &&
                        selectedSecondarySet.has(secondary);
                      const tertiaries = secondaryActsAsLeaf ? [] : (innerTertiary?.get(secondary) || []);
                      const isExpandedSec = !!expandedSecondaries[secondaryKey(primary, secondary)];
                      const hasTertiaries = !secondaryActsAsLeaf && tertiaries.length > 0;

                      return (
                        <div key={secondary}>
                          <div className="flex items-center gap-1">
                            <button
                              onClick={() => {
                                if (isActivityMetrics) {
                                  onSelectTertiary(primary, secondary, secondary);
                                  return;
                                }
                                onSelectSecondary(primary, secondary);
                              }}
                              className={`flex-1 min-w-0 flex items-center text-left px-3 py-2 rounded-lg text-sm transition-[color,background-color,border-color,box-shadow] duration-150 ease-[var(--motion-fluid)] ${
                                isSelectedSecondary
                                  ? "bg-[#EFF6FF] border border-[#BFDBFE] text-[#0F172A] font-medium"
                                  : "bg-transparent border border-transparent text-[#64748B] hover:bg-slate-50"
                              }`}
                              title={secondary}
                            >
                              <span className="truncate block w-full">{secondary}</span>
                            </button>

                            {hasTertiaries ? (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toggleSecondaryExpand(primary, secondary);
                                }}
                                className="p-2 rounded-lg text-[#64748B] hover:bg-slate-50 shrink-0"
                                aria-label={secondary}
                              >
                                <ChevronRight
                                  className={`w-3.5 h-3.5 transition-transform ${isExpandedSec ? "rotate-90" : ""}`}
                                />
                              </button>
                            ) : null}
                          </div>

                          {hasTertiaries && isExpandedSec && (
                            <div className="ml-3 mt-0.5 space-y-0.5">
                              {tertiaries.map((metricName) => {
                                const isSelectedMetric =
                                  directSelection?.level === "tertiary" &&
                                  directSelection.primary === primary &&
                                  directSelection.secondary === secondary &&
                                  directSelection.metricName === metricName &&
                                  safeTrim(selectedPrimary) === primary &&
                                  selectedSecondarySet.has(secondary) &&
                                  selectedTertiary === metricName;
                                return (
                                  <button
                                    key={metricName}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      onSelectTertiary(primary, secondary, metricName);
                                    }}
                                    className={`w-full text-left px-3 py-1.5 rounded-md text-xs transition-[color,background-color,border-color,box-shadow] duration-150 ease-[var(--motion-fluid)] ${
                                      isSelectedMetric
                                        ? "bg-[#DBEAFE] border border-[#93C5FD] text-[#1E40AF] font-medium"
                                        : "bg-transparent border border-transparent text-[#64748B] hover:bg-slate-50"
                                    }`}
                                    title={metricName}
                                  >
                                    <span className="block truncate">{metricName}</span>
                                  </button>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })
                  ) : (
                    <div className="px-3 py-2 text-xs text-slate-400">{t("crossAnalysis.noSecondaryNav")}</div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

    </div>
  );
}
