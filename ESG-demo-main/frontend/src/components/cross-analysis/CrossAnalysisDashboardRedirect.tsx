"use client";

import React, { useEffect, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { isAuthenticated } from "@/lib/auth";

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

/**
 * Cross Analysis preflight gate:
 * - If framework is missing, DO NOT render cross-analysis UI.
 * - Redirect to dashboard and launch framework picker there, so dashboard is the background.
 */
export default function CrossAnalysisDashboardRedirect({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname() || "";
  const searchParams = useSearchParams();

  const isEvidenceRoute = useMemo(
    () => pathname.includes("/cross-analysis/evidence"),
    [pathname],
  );

  const qsString = useMemo(() => searchParams.toString(), [searchParams]);
  const framework = useMemo(() => safeTrim(searchParams.get("framework")), [searchParams]);

  const shouldRedirect = !isEvidenceRoute && !framework;

  useEffect(() => {
    if (!shouldRedirect) return;
    // Auth is enforced by dashboard layout, but keep behavior consistent for direct deep links.
    if (!isAuthenticated()) {
      router.replace("/login");
      return;
    }

    const fullTarget = `${pathname}${qsString ? `?${qsString}` : ""}`;
    const dash = `/dashboard?launch=cross-analysis&target=${encodeURIComponent(fullTarget)}`;
    router.replace(dash);
  }, [pathname, qsString, router, shouldRedirect]);

  // Return nothing while redirecting so users never see cross-analysis as the background.
  if (shouldRedirect) return null;
  return <>{children}</>;
}
