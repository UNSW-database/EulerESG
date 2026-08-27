"use client";

import { ChevronLeft, ChevronRight, Filter, X } from "lucide-react";
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useT } from "@/i18n/useT";

interface DataRow {
  id: number;
  report: string;
  metric: string;
  detail: string;
  year: number;
  value: string;
  unit: string;
  fileId?: string;
  page?: number | null;
  isNotDisclosed?: boolean;
}

interface NewDataTableProps {
  data: DataRow[];
  onViewEvidence?: (row: DataRow) => void;
}

function norm(v: any) {
  return String(v ?? "").trim();
}

function uniqSorted(arr: string[]) {
  return Array.from(new Set(arr.filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

type DropdownPosition = { top: number; left: number; width: number };

function useDropdownPosition(open: boolean, anchorRef: React.RefObject<HTMLElement>, width = 248) {
  const [pos, setPos] = useState<DropdownPosition>({ top: 0, left: 0, width });

  useLayoutEffect(() => {
    if (!open || !anchorRef.current || typeof window === "undefined") return;

    const update = () => {
      const rect = anchorRef.current!.getBoundingClientRect();
      const desiredWidth = width;
      const maxLeft = Math.max(12, window.innerWidth - desiredWidth - 12);
      const left = Math.min(Math.max(12, rect.left), maxLeft);
      const top = rect.bottom + 8;
      setPos({ top, left, width: desiredWidth });
    };

    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, anchorRef, width]);

  return pos;
}

type MultiFilterProps = {
  ariaLabel: string;
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
};

function MultiSelectFilter({ ariaLabel, options, selected, onChange }: MultiFilterProps) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const pos = useDropdownPosition(open, anchorRef as React.RefObject<HTMLElement>, 252);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!open) return;
      const target = e.target as Node;
      if (anchorRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const toggle = (opt: string) => {
    if (selected.includes(opt)) {
      onChange(selected.filter((x) => x !== opt));
    } else {
      onChange([...selected, opt]);
    }
  };

  return (
    <div ref={anchorRef} className="inline-flex">
      <button
        type="button"
        aria-label={ariaLabel}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={`p-1 rounded hover:bg-slate-100 transition-colors ${selected.length ? "text-[#3B82F6]" : "text-[#CBD5E1]"}`}
      >
        <Filter className="w-3.5 h-3.5" />
      </button>

      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={panelRef}
              className="fixed bg-white border border-slate-200 rounded-xl shadow-lg p-3 z-[1200]"
              style={{ top: pos.top, left: pos.left, width: pos.width, maxHeight: "72vh" }}
            >
              <div className="space-y-1.5 overflow-y-auto overflow-x-visible overscroll-y-auto pr-1" style={{ maxHeight: "56vh" }}>
                {options.length === 0 ? (
                  <div className="text-sm text-slate-500 py-2 text-center">{t("common.noOptions")}</div>
                ) : (
                  options.map((opt) => (
                    <label key={opt} className="flex items-start gap-2 py-1.5 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={selected.includes(opt)}
                        onChange={() => toggle(opt)}
                        className="accent-blue-500 mt-0.5 shrink-0"
                      />
                      <span className="text-sm text-slate-700 break-words text-left leading-5">{opt}</span>
                    </label>
                  ))
                )}
              </div>

              <div className="flex items-center justify-between mt-3 pt-2 border-t border-slate-200">
                <button type="button" onClick={() => onChange([])} className="text-sm text-slate-600 hover:text-slate-900">
                  {t("common.clear")}
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="px-3 py-1.5 rounded-lg bg-[#3B82F6] text-white text-sm font-medium hover:bg-[#2563EB]"
                >
                  {t("common.apply")}
                </button>
              </div>
            </div>,
            document.body
          )
        : null}
    </div>
  );
}

type TextFilterProps = {
  ariaLabel: string;
  value: string;
  onChange: (next: string) => void;
};

function TextFilter({ ariaLabel, value, onChange }: TextFilterProps) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const pos = useDropdownPosition(open, anchorRef as React.RefObject<HTMLElement>, 252);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!open) return;
      const target = e.target as Node;
      if (anchorRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={anchorRef} className="inline-flex">
      <button
        type="button"
        aria-label={ariaLabel}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={`p-1 rounded hover:bg-slate-100 transition-colors ${value.trim() ? "text-[#3B82F6]" : "text-[#CBD5E1]"}`}
      >
        <Filter className="w-3.5 h-3.5" />
      </button>

      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={panelRef}
              className="fixed bg-white border border-slate-200 rounded-xl shadow-lg p-3 z-[1200]"
              style={{ top: pos.top, left: pos.left, width: pos.width }}
            >
              <div className="text-xs font-semibold text-slate-600 mb-2 text-left">{t("common.contains")}</div>
              <div className="flex items-center gap-1.5">
                <input
                  value={value}
                  onChange={(e) => onChange(e.target.value)}
                  placeholder={t("common.typeToFilter")}
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-slate-400"
                />
                <button
                  type="button"
                  aria-label={t("common.clear")}
                  onClick={() => onChange("")}
                  className="p-2 rounded-lg hover:bg-slate-100 text-slate-500"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="flex items-center justify-end mt-3 pt-2 border-t border-slate-200">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="px-3 py-1.5 rounded-lg bg-[#3B82F6] text-white text-sm font-medium hover:bg-[#2563EB]"
                >
                  {t("common.apply")}
                </button>
              </div>
            </div>,
            document.body
          )
        : null}
    </div>
  );
}

export function NewDataTable({ data, onViewEvidence }: NewDataTableProps) {
  const { t } = useT();
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 12;

  const reportOptions = useMemo(() => uniqSorted(data.map((d) => norm(d.report))), [data]);
  const metricOptions = useMemo(() => uniqSorted(data.map((d) => norm(d.metric))), [data]);
  const yearOptions = useMemo(
    () => Array.from(new Set(data.map((d) => String(d.year)).filter(Boolean))).sort((a, b) => Number(a) - Number(b)),
    [data]
  );
  const unitOptions = useMemo(() => uniqSorted(data.map((d) => norm(d.unit))), [data]);

  const [reportFilter, setReportFilter] = useState<string[]>([]);
  const [metricFilter, setMetricFilter] = useState<string[]>([]);
  const [yearFilter, setYearFilter] = useState<string[]>([]);
  const [unitFilter, setUnitFilter] = useState<string[]>([]);
  const [detailQuery, setDetailQuery] = useState<string>("");
  const [valueQuery, setValueQuery] = useState<string>("");

  useEffect(() => setCurrentPage(1), [data.length]);
  useEffect(() => setCurrentPage(1), [reportFilter, metricFilter, yearFilter, unitFilter, detailQuery, valueQuery]);

  const filteredData = useMemo(() => {
    const dq = detailQuery.trim().toLowerCase();
    const vq = valueQuery.trim().toLowerCase();

    return data.filter((row) => {
      if (reportFilter.length && !reportFilter.includes(norm(row.report))) return false;
      if (metricFilter.length && !metricFilter.includes(norm(row.metric))) return false;
      if (yearFilter.length && !yearFilter.includes(String(row.year))) return false;
      if (unitFilter.length && !unitFilter.includes(norm(row.unit))) return false;

      if (dq) {
        const hay = `${norm(row.detail)} ${norm(row.metric)}`.toLowerCase();
        if (!hay.includes(dq)) return false;
      }
      if (vq) {
        const hay = `${norm(row.value)} ${norm(row.unit)}`.toLowerCase();
        if (!hay.includes(vq)) return false;
      }
      return true;
    });
  }, [data, reportFilter, metricFilter, yearFilter, unitFilter, detailQuery, valueQuery]);

  const totalPages = Math.ceil(filteredData.length / itemsPerPage) || 1;
  const startIndex = (currentPage - 1) * itemsPerPage;
  const endIndex = startIndex + itemsPerPage;
  const currentData = filteredData.slice(startIndex, endIndex);

  const goToNextPage = () => {
    if (currentPage < totalPages) setCurrentPage((p) => p + 1);
  };

  const goToPreviousPage = () => {
    if (currentPage > 1) setCurrentPage((p) => p - 1);
  };

  return (
    <div className="bg-white rounded-2xl shadow-sm p-5 w-full overflow-visible">
      <div className="w-full overflow-x-auto overflow-y-visible">
        <table className="w-full table-fixed min-w-[1080px] overflow-visible">
          <thead>
            <tr className="border-b border-[#E2E8F0]">
              <th className="text-center py-2 px-2 text-sm font-semibold text-[#64748B] w-[11%] whitespace-nowrap overflow-visible">
                <div className="flex items-center justify-center gap-1.5">
                  <span>{t("crossAnalysis.table.report")}</span>
                  <MultiSelectFilter
                    ariaLabel={t("crossAnalysis.table.filterReport")}
                    options={reportOptions}
                    selected={reportFilter}
                    onChange={setReportFilter}
                  />
                </div>
              </th>

              <th className="text-center py-2 px-2 text-sm font-semibold text-[#64748B] w-[18%] whitespace-nowrap overflow-visible">
                <div className="flex items-center justify-center gap-1.5">
                  <span>{t("crossAnalysis.table.metric")}</span>
                  <MultiSelectFilter
                    ariaLabel={t("crossAnalysis.table.filterMetric")}
                    options={metricOptions}
                    selected={metricFilter}
                    onChange={setMetricFilter}
                  />
                </div>
              </th>

              <th className="text-center py-2 px-2 text-sm font-semibold text-[#64748B] w-[4%] whitespace-nowrap overflow-visible">
                <div className="flex items-center justify-center gap-1.5">
                  <span>{t("crossAnalysis.table.year")}</span>
                  <MultiSelectFilter
                    ariaLabel={t("crossAnalysis.table.filterYear")}
                    options={yearOptions}
                    selected={yearFilter}
                    onChange={setYearFilter}
                  />
                </div>
              </th>

              <th className="text-center py-2 px-2 text-sm font-semibold text-[#64748B] w-[11%] whitespace-nowrap overflow-visible">
                <div className="flex items-center justify-center gap-1.5">
                  <span>{t("crossAnalysis.table.value")}</span>
                  <TextFilter ariaLabel={t("crossAnalysis.table.filterValue")} value={valueQuery} onChange={setValueQuery} />
                </div>
              </th>

              <th className="text-center py-2 px-2 text-sm font-semibold text-[#64748B] w-[10%] whitespace-nowrap overflow-visible">
                <div className="flex items-center justify-center gap-1.5">
                  <span>{t("crossAnalysis.table.unit")}</span>
                  <MultiSelectFilter
                    ariaLabel={t("crossAnalysis.table.filterUnit")}
                    options={unitOptions}
                    selected={unitFilter}
                    onChange={setUnitFilter}
                  />
                </div>
              </th>

              <th className="text-center py-2 px-2 text-sm font-semibold text-[#64748B] w-[40%] whitespace-nowrap overflow-visible">
                <div className="flex items-center justify-center gap-1.5">
                  <span>{t("crossAnalysis.table.detail")}</span>
                  <TextFilter ariaLabel={t("crossAnalysis.table.filterDetail")} value={detailQuery} onChange={setDetailQuery} />
                </div>
              </th>

              <th className="text-center py-2 px-2 text-sm font-semibold text-[#64748B] w-[6%] whitespace-nowrap">
                {t("crossAnalysis.table.evidence")}
              </th>
            </tr>
          </thead>

          <tbody>
            {currentData.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-8 text-center text-[#64748B]">
                  {t("common.noDataAvailable")}
                </td>
              </tr>
            ) : (
              currentData.map((row, index) => (
                <tr
                  key={row.id}
                  className={`border-b border-[#E2E8F0] ${index % 2 === 0 ? "bg-white" : "bg-[#F8FAFC]"} hover:bg-[#F1F5F9] transition-colors`}
                >
                  <td className="py-2 px-2 text-sm text-[#0F172A] break-words whitespace-normal text-center align-middle">{row.report}</td>
                  <td className="py-2 px-2 text-sm text-[#0F172A] break-words whitespace-normal text-center align-middle">{row.metric}</td>
                  <td className="py-2 px-2 text-sm text-[#0F172A] text-center align-middle">{row.year}</td>
                  <td className="py-2 px-2 text-sm break-words whitespace-normal text-center align-middle">
                    {(row as DataRow).isNotDisclosed ? (
                      <span
                        className="italic text-slate-400 font-normal inline-flex items-center gap-1 justify-center"
                        title={t("crossAnalysis.notDisclosed")}
                      >
                        N/D
                        <span className="text-slate-400" aria-hidden>ⓘ</span>
                      </span>
                    ) : (
                      <span className="font-bold text-[#0F172A]">{row.value}</span>
                    )}
                  </td>
                  <td className="py-2 px-2 text-sm text-[#64748B] break-words whitespace-normal text-center align-middle">{row.unit}</td>
                  <td className="py-2 px-2 text-sm text-[#64748B] break-words whitespace-normal text-center align-middle">{row.detail}</td>
                  <td className="py-2 px-2 text-center align-middle">
                    <button
                      className="text-sm text-[#3B82F6] hover:text-[#2563EB] font-medium"
                      onClick={() => onViewEvidence?.(row)}
                    >
                      {t("common.view")}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between mt-5 pt-3 border-t border-[#E2E8F0]">
        <p className="text-sm text-[#64748B]">
          {filteredData.length === 0 ? (
            <>{t("common.showingZeroEntries")}</>
          ) : (
            <>{t("common.showingRange", { from: startIndex + 1, to: Math.min(endIndex, filteredData.length), total: filteredData.length })}</>
          )}
        </p>

        <div className="flex items-center justify-center gap-1.5">
          <button
            onClick={goToPreviousPage}
            disabled={currentPage === 1}
            className={`p-2 rounded-lg border border-[#E2E8F0] ${currentPage === 1 ? "text-[#CBD5E1] cursor-not-allowed" : "text-[#64748B] hover:bg-[#F8FAFC]"}`}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>

          {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
            <button
              key={page}
              onClick={() => setCurrentPage(page)}
              className={`px-2.5 py-1.5 rounded-lg text-sm font-medium ${currentPage === page ? "bg-[#3B82F6] text-white" : "text-[#64748B] hover:bg-[#F8FAFC]"}`}
            >
              {page}
            </button>
          ))}

          <button
            onClick={goToNextPage}
            disabled={currentPage === totalPages}
            className={`p-2 rounded-lg border border-[#E2E8F0] ${currentPage === totalPages ? "text-[#CBD5E1] cursor-not-allowed" : "text-[#64748B] hover:bg-[#F8FAFC]"}`}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
