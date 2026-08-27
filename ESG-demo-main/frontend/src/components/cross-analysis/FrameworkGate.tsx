"use client";

import React, { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import FrameworkSelectModal, {
  type FrameworkSelectionValues,
} from "@/components/cross-analysis/FrameworkSelectModal";
import { getDefaultDimensionKey } from "@/data/crossTaxonomy";
import { isActiveFramework } from "@/data/frameworkOptions";
import { isAuthenticated } from "@/lib/auth";
import { applyFrameworkToSearchParams } from "@/lib/crossAnalysisFramework";

/**
 * A thin route guard for Cross Analysis routes.
 *
 * Why this exists:
 * - Many entry points push directly to `/cross-analysis/[dimension]?...` (e.g. PDFViewer),
 *   bypassing `/cross-analysis`.
 * - The user experience requires choosing a framework before the comparison pages render.
 */

const LS_KEY = "cross_analysis_framework_selection";

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

export default function FrameworkGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() || "";
  const searchParams = useSearchParams();

  const isEvidenceRoute = useMemo(() => pathname.includes("/cross-analysis/evidence"), [pathname]);

  const qsString = useMemo(() => searchParams.toString(), [searchParams]);
  const framework = useMemo(() => safeTrim(searchParams.get("framework")), [searchParams]);

  const [open, setOpen] = useState(false);
  const [initialValues, setInitialValues] = useState<Partial<FrameworkSelectionValues>>({});

  useEffect(() => {
    // Do not gate evidence drill-down pages; those are opened in a new tab and should be frictionless.
    if (isEvidenceRoute) {
      setOpen(false);
      return;
    }

    if (isActiveFramework(framework)) {
      setOpen(false);
      return;
    }

    // Load last selection (best-effort).
    try {
      const raw = typeof window !== "undefined" ? localStorage.getItem(LS_KEY) : null;
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          const cachedFramework = safeTrim((parsed as any).framework);
          if (isActiveFramework(cachedFramework)) {
            setInitialValues({
              framework: cachedFramework.toUpperCase(),
              industry: safeTrim((parsed as any).industry) || undefined,
              semiIndustry: safeTrim((parsed as any).semiIndustry) || undefined,
            });
          } else {
            localStorage.removeItem(LS_KEY);
            setInitialValues({});
          }
        }
      }
    } catch {
      // ignore
    }

    setOpen(true);
  }, [framework, isEvidenceRoute, pathname]);

  const handleConfirm = (values: FrameworkSelectionValues) => {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(values));
    } catch {
      // ignore
    }

    const qs = applyFrameworkToSearchParams(new URLSearchParams(qsString), values);
    const nextQs = qs.toString();

    // If we are at the Cross Analysis entry route, redirect to the default dimension.
    const nextPath = pathname === "/cross-analysis" ? `/cross-analysis/${getDefaultDimensionKey()}` : pathname;
    router.replace(`${nextPath}${nextQs ? `?${nextQs}` : ""}`);
    setOpen(false);
  };

  const handleCancel = () => {
    setOpen(false);
    router.replace(isAuthenticated() ? "/dashboard" : "/login");
  };

  return (
    <>
      <FrameworkSelectModal
        open={open}
        initialValues={initialValues}
        onCancel={handleCancel}
        onConfirm={handleConfirm}
      />

      {/* Render children underneath; modal will visually block interactions when open */}
      {children}
    </>
  );
}
