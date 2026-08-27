"use client";

import React, { Suspense, useMemo, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { Card, Typography } from "antd";
import { crossTokens } from "@/features/crossAnalysis/tokens";
import { getStoredAuth } from "@/lib/auth";
import { useT } from "@/i18n/useT";

const { Title, Text } = Typography;

const PDFEvidenceViewer = dynamic(
  () => import("@/components/pdfviewer/PDFEvidenceViewer"),
  {
    ssr: false,
    loading: () => (
      <div
        data-testid="pdf-evidence-loading"
        aria-busy="true"
        aria-label="Loading PDF evidence"
        className="min-h-[70vh] w-full animate-pulse rounded-xl bg-slate-100"
      />
    ),
  },
);

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "";

function asInt(v: string | null, fallback: number) {
  if (!v) return fallback;
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.floor(n));
}

function safeDecode(v: string) {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}

function isUuid(v: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(v.trim());
}

function normalizeKey(v: any): string {
  if (!v) return "";
  const s = String(v).trim().toLowerCase();
  const noExt = s.replace(/\.(pdf|json|txt)$/i, "");
  return noExt.replace(/[^a-z0-9]+/g, "");
}

async function resolveReportId(aliasOrId: string): Promise<string | null> {
  const wanted = normalizeKey(aliasOrId);
  if (!wanted) return null;
  const auth = getStoredAuth();
  const headers: Record<string, string> = {};
  if (auth?.token) headers["Authorization"] = `Bearer ${auth.token}`;

  const res = await fetch(`${API_BASE_URL}/api/files?file_type=report`, { headers, cache: "no-store" });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `HTTP ${res.status}`);
  }
  const payload: any = await res.json();
  const files: any[] = Array.isArray(payload?.files) ? payload.files : [];

  let best: any = null;
  for (const f of files) {
    const fid = String(f?.file_id || "");
    const cands = [fid, f?.original_name, f?.safe_filename, f?.file_path];
    if (cands.some((c) => normalizeKey(c) === wanted)) {
      best = f;
      break;
    }
  }

  return best?.file_id ? String(best.file_id) : null;
}

function CrossEvidencePageContent() {
  const { t } = useT();
  const sp = useSearchParams();

  useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    html.classList.add("evidence-x-scroll");
    body.classList.add("evidence-x-scroll");
    return () => {
      html.classList.remove("evidence-x-scroll");
      body.classList.remove("evidence-x-scroll");
    };
  }, []);

  const fileId = safeDecode(sp.get("file_id") || "");
  const page = asInt(sp.get("page"), 1);
  const name = safeDecode(sp.get("name") || t("crossAnalysis.evidence.defaultName"));

  const [resolvedFileId, setResolvedFileId] = useState<string>(fileId);
  const [resolving, setResolving] = useState<boolean>(false);
  const [resolveError, setResolveError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      setResolvedFileId(fileId);
      setResolveError(null);
      if (!fileId) return;
      if (isUuid(fileId)) return;
      setResolving(true);
      try {
        const resolved = await resolveReportId(fileId);
        if (alive && resolved) setResolvedFileId(resolved);
      } catch (e: any) {
        if (alive) setResolveError(e?.message || t("common.error"));
      } finally {
        if (alive) setResolving(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [fileId, t]);

  const fileUrl = useMemo(() => `${API_BASE_URL}/api/files/${encodeURIComponent(resolvedFileId)}/pdf`, [resolvedFileId]);

  if (!fileId) {
    return (
      <div style={{ minHeight: "100vh", width: "100%", background: crossTokens.color.bg, padding: crossTokens.spacing.xl }}>
        <Card style={{ borderRadius: crossTokens.radius.card, border: `1px solid ${crossTokens.color.border}` }}>
          <Title level={4} style={{ marginTop: 0 }}>{t("crossAnalysis.evidence.missingFileIdTitle")}</Title>
          <Text style={{ color: crossTokens.color.subtext }}>
            {t("crossAnalysis.evidence.openFromLink")}
          </Text>
        </Card>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", width: "100%", background: crossTokens.color.bg, padding: 12 }}>
      <div style={{ maxWidth: "100%", margin: "0 auto", width: "100%" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
          <div>
            <Title level={3} style={{ margin: 0, color: crossTokens.color.text }}>{name}</Title>
            {resolving ? (
              <div><Text style={{ color: crossTokens.color.subtext }}>{t("crossAnalysis.evidence.resolvingReportId")}</Text></div>
            ) : resolveError ? (
              <div><Text style={{ color: "#c0362c" }}>{t("crossAnalysis.evidence.failedToLoadPdf", { error: resolveError })}</Text></div>
            ) : null}
          </div>
        </div>

        <div style={{ height: 12 }} />

        <Card
          style={{
            borderRadius: crossTokens.radius.card,
            border: `1px solid ${crossTokens.color.border}`,
            boxShadow: crossTokens.shadow.card,
            background: crossTokens.color.card,
            overflow: "visible",
          }}
          styles={{ body: { padding: 10, overflow: "visible" } }}
        >
          <PDFEvidenceViewer
            fileUrl={fileUrl}
            initialPage={page}
            scrollMode="page"
            fitTo="width"
            defaultZoom={1.05}
          />
        </Card>
      </div>
    </div>
  );
}

export default function CrossEvidencePage() {
  return (
    <Suspense
      fallback={(
        <div
          aria-busy="true"
          style={{ minHeight: "100vh", width: "100%", background: crossTokens.color.bg }}
        />
      )}
    >
      <CrossEvidencePageContent />
    </Suspense>
  );
}
