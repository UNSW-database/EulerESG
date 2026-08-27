"use client";

import React, { useEffect, useMemo, useState } from "react";

import { useRouter, useSearchParams } from "next/navigation";

import FrameworkSelectModal, {
  type FrameworkSelectionValues,
} from "@/components/cross-analysis/FrameworkSelectModal";
import { isAuthenticated } from "@/lib/auth";
import {
  applyFrameworkToSearchParams,
  readCachedCrossFrameworkSelection,
  safeDecodeURIComponent,
  writeCachedCrossFrameworkSelection,
} from "@/lib/crossAnalysisFramework";

/**
 * Launches the Cross Analysis framework picker *on the dashboard*.
 *
 * Trigger:
 *   /dashboard?launch=cross-analysis&ids=...&target=/cross-analysis/environment?ids=...
 *
 * Why dashboard:
 *   Product requirement is that the framework chooser appears before any cross-analysis page
 *   is shown, and the background should be the dashboard (not the cross-analysis layout).
 */
export default function CrossAnalysisFrameworkLauncher() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const launch = useMemo(() => (searchParams.get("launch") || "").trim(), [searchParams]);
  const shouldOpen = launch === "cross-analysis";

  const [open, setOpen] = useState(false);
  const [initialValues, setInitialValues] = useState<Partial<FrameworkSelectionValues>>({});

  useEffect(() => {
    if (!shouldOpen) {
      setOpen(false);
      return;
    }
    setInitialValues(readCachedCrossFrameworkSelection());
    setOpen(true);
  }, [shouldOpen]);

  const handleCancel = () => {
    setOpen(false);
    router.replace(isAuthenticated() ? "/dashboard" : "/login");
  };

  const buildTargetUrl = (values: FrameworkSelectionValues): string => {
    const targetRaw = (searchParams.get("target") || "").trim();
    const ids = (searchParams.get("ids") || "").trim();

    const target = targetRaw ? safeDecodeURIComponent(targetRaw) : "/cross-analysis/environment";
    const url = new URL(target, "http://local");

    let qs = new URLSearchParams(url.searchParams.toString());

    // Convenience: if ids were provided at launch time but not included in the target, merge them.
    if (ids && !qs.get("ids")) qs.set("ids", ids);

    qs = applyFrameworkToSearchParams(qs, values);

    const nextQs = qs.toString();
    return `${url.pathname}${nextQs ? `?${nextQs}` : ""}`;
  };

  const handleConfirm = (values: FrameworkSelectionValues) => {
    writeCachedCrossFrameworkSelection(values);
    const next = buildTargetUrl(values);

    // Use replace so that Back returns to a clean dashboard URL (without relaunching the modal).
    router.replace(next);
    setOpen(false);
  };

  if (!shouldOpen) return null;

  return (
    <FrameworkSelectModal
      open={open}
      initialValues={initialValues}
      onCancel={handleCancel}
      onConfirm={handleConfirm}
    />
  );
}
