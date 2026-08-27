"use client";

import React, { Suspense, useEffect, useMemo } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import {
  buildComplianceAnalysisHref,
  useFileStore,
} from "@/store/useFileStore";
import { useEnsureReportFiles } from "@/hooks/useEnsureReportFiles";

function ChatWorkspaceLoading() {
  return (
    <div
      data-testid="chat-workspace-loading"
      aria-busy="true"
      className="min-h-screen w-full bg-slate-50 px-6 py-5"
    >
      <div className="mx-auto w-[95%] animate-pulse space-y-5">
        <div className="h-96 rounded-2xl border border-slate-200 bg-white" />
        <div className="h-[600px] rounded-2xl border border-slate-200 bg-white" />
      </div>
    </div>
  );
}

const ChatView = dynamic(
  () => import("@/components/pdfviewer/ChatView"),
  { ssr: false, loading: ChatWorkspaceLoading },
);

function ChatPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const files = useFileStore((state) => state.files);
  const selectedFileId = useFileStore((state) => state.selectedFileId);
  const selectedFileScopeKey = useFileStore(
    (state) => state.selectedFileScopeKey,
  );
  const setComplianceSelection = useFileStore(
    (state) => state.setComplianceSelection,
  );
  useEnsureReportFiles();

  const queryFileId = searchParams.get("file_id");
  const queryScope = searchParams.get("scope");
  const requestedFileId = queryFileId || selectedFileId || undefined;
  const requestedScopeKey = queryFileId
    ? (searchParams.has("scope") ? queryScope?.trim() || undefined : undefined)
    : selectedFileScopeKey || undefined;

  const currentFile = useMemo(() => {
    if (!requestedFileId) return null;
    const candidates = files.filter((file) => file.file_id === requestedFileId);
    if (candidates.length === 0) return null;
    if (requestedScopeKey) {
      const scopedFile = candidates.find(
        (file) => file.analysis_scope_key === requestedScopeKey,
      );
      return scopedFile || null;
    }
    if (candidates.length === 1) return candidates[0];
    const unscopedCandidates = candidates.filter(
      (file) => !file.analysis_scope_key,
    );
    return unscopedCandidates.length === 1 ? unscopedCandidates[0] : null;
  }, [files, requestedFileId, requestedScopeKey]);

  useEffect(() => {
    if (!queryFileId || !currentFile?.file_id) return;
    setComplianceSelection(
      currentFile.file_id,
      currentFile.analysis_scope_key,
    );
  }, [currentFile, queryFileId, setComplianceSelection]);

  useEffect(() => {
    if (!currentFile?.file_id) return;
    const currentScopeKey = String(queryScope || "").trim();
    const resolvedScopeKey = String(
      currentFile.analysis_scope_key || "",
    ).trim();
    if (
      queryFileId === currentFile.file_id
      && currentScopeKey === resolvedScopeKey
    ) {
      return;
    }
    router.replace(
      buildComplianceAnalysisHref(
        currentFile.file_id,
        currentFile.analysis_scope_key,
      ),
    );
  }, [currentFile, queryFileId, queryScope, router]);

  return (
    <div className="mx-auto flex min-h-screen w-full flex-col items-center justify-start pt-1">
      <div className="w-[95%]">
        <ChatView
          activeFile={currentFile}
          fileId={currentFile?.file_id}
          scopeKey={currentFile?.analysis_scope_key}
        />
      </div>
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={<div aria-busy="true" className="min-h-screen w-full bg-white" />}
    >
      <ChatPageContent />
    </Suspense>
  );
}
