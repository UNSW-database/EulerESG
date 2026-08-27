"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useT } from "@/i18n/useT";
import { apiService } from "@/lib/api";
import {
  buildComplianceAnalysisHref,
  useFileStore,
} from "@/store/useFileStore";
import type { File } from "@/store/useFileStore";
import { useEnsureReportFiles } from "@/hooks/useEnsureReportFiles";

const FileTable = dynamic(
  () => import("@/components/pdfviewer/FileTable"),
  {
    ssr: false,
    loading: () => (
      <div
        data-testid="favourite-table-loading"
        aria-busy="true"
        className="mt-4 h-96 animate-pulse rounded-2xl border border-slate-200 bg-white"
      />
    ),
  },
);

export default function FavouriteReportsPage() {
  const router = useRouter();
  const { lang } = useT();
  const [selectedRows, setSelectedRows] = useState<File[]>([]);
  useEnsureReportFiles();

  useEffect(() => {
    router.prefetch("/dashboard/chat");
  }, [router]);

  const openAnalysis = useCallback((file: File) => {
    if (!file.file_id) return;
    useFileStore.getState().setComplianceSelection(
      file.file_id,
      file.analysis_scope_key,
    );
    apiService.prefetchAssessmentByFile(
      file.file_id,
      file.analysis_scope_key,
      false,
      true,
    );

    router.push(
      buildComplianceAnalysisHref(file.file_id, file.analysis_scope_key),
    );
  }, [router]);

  return (
    <main className="min-h-screen w-full bg-gray-50 pt-1">
      <div className="mx-auto w-[95%]">
        <FileTable
          onChatClick={openAnalysis}
          selectedRows={selectedRows}
          onSelectionChange={setSelectedRows}
          reportCatalogMode="single"
          favouritesOnly
          title={lang === "zh" ? "收藏报告" : "Favourite reports"}
          emptyText={
            lang === "zh"
              ? "暂无收藏报告。可在主页报告右侧的更多操作中添加收藏。"
              : "No favourite reports yet. Add one from a report's More actions menu on the homepage."
          }
        />
      </div>
    </main>
  );
}
