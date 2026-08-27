"use client";

import React, { useState } from "react";
import dynamic from "next/dynamic";
import { App as AntdApp, Form, Progress } from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import { useFileStore } from "@/store/useFileStore";
import type { ReportCatalogMode } from "@/store/useFileStore";
import { apiService } from "@/lib/api";
import UploadArea from "./UploadArea";
import type { FileInfoFormValues } from "./FileInfoForm";
import { isActiveFramework } from "@/data/frameworkOptions";
import { useT } from "@/i18n/useT";

const UploadOptionsModal = dynamic(
  () => import("./UploadOptionsModal"),
  { ssr: false },
);

type ActiveReportJob = {
  jobId: string;
  fileName: string;
  status: string;
  stage?: string;
  message?: string;
  progress?: number;
  error?: string | null;
};

function normStrList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => String(x).trim()).filter(Boolean);
  if (v != null && String(v).trim()) return [String(v).trim()];
  return [];
}

function userFacingJobMessage(event: any): string {
  const status = String(event?.status || "").toLowerCase();
  const stage = String(event?.stage || "").toLowerCase();

  if (status === "failed" || stage === "failed") {
    return "Processing failed. Please try again.";
  }
  if (status === "success" || status === "partial_success" || stage === "completed") {
    return "Processing completed.";
  }

  const stageMessages: Record<string, string> = {
    queued: "Waiting to start.",
    started: "Starting document processing.",
    saving: "Uploading document.",
    file_saved: "Document uploaded.",
    pdf_processing: "Reading document content.",
    ocr_start: "Reading document content.",
    ocr_queued: "Reading document content.",
    ocr_batch_processing: "Reading document content.",
    ocr_merging: "Organizing extracted content.",
    pdf_processed: "Document content extracted.",
    summary_ready: "Preparing summary.",
    assessment_start: "Starting disclosure assessment.",
    assessment_scope: "Analyzing disclosure information.",
    assessment_scope_done: "Disclosure assessment updated.",
    completed: "Processing completed.",
  };

  return stageMessages[stage] || "Processing document.";
}

function formatReportJobMessage(fileName: string, event: any): string {
  const progress = typeof event?.progress === "number" ? ` ${Math.round(event.progress)}%` : "";
  return `${fileName}: ${userFacingJobMessage(event)}${progress}`;
}

interface MainContentProps {
  uploadMode: ReportCatalogMode;
}

const MainContent: React.FC<MainContentProps> = ({ uploadMode }) => {
  const { t } = useT();
  const { message } = AntdApp.useApp();

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedUploadFiles, setSelectedUploadFiles] = useState<UploadFile[]>([]);
  const [selectedIndustry, setSelectedIndustry] = useState<string>("");
  const [uploadSubmitting, setUploadSubmitting] = useState(false);
  const [activeReportJobs, setActiveReportJobs] = useState<Record<string, ActiveReportJob>>({});
  const [form] = Form.useForm<FileInfoFormValues>();

  const handleBeforeUpload = (files: UploadFile[]) => {
    if (!files.length) return;
    if (uploadMode === "single" && files.length !== 1) {
      void message.error(t("upload.singleReportLimit"));
      return;
    }
    if (uploadMode === "multi" && (files.length < 2 || files.length > 8)) {
      void message.error(t("upload.multiReportLimit"));
      return;
    }
    setSelectedUploadFiles(files);
    setIsModalOpen(true);
  };

  const handleModalOk = async () => {
    if (uploadSubmitting) return;
    let batchMessageKey: string | null = null;
    setUploadSubmitting(true);
    try {
      await form.validateFields();
      const queuedFiles = [...selectedUploadFiles];
      const values = form.getFieldsValue();
      if (!isActiveFramework(values.framework)) {
        throw new Error(t("upload.pleaseSelectFramework"));
      }

      if (!queuedFiles.length) {
        setIsModalOpen(false);
        form.resetFields();
        return;
      }
      if (uploadMode === "single" && queuedFiles.length !== 1) {
        throw new Error(t("upload.singleReportLimit"));
      }
      if (uploadMode === "multi" && (queuedFiles.length < 2 || queuedFiles.length > 8)) {
        throw new Error(t("upload.multiReportLimit"));
      }

      const isGRI = values.framework === "GRI";
      const isCDP = values.framework === "CDP";
      const griTopics = normStrList(values.griTopics);
      const semiVals = normStrList(values.semiIndustry);
      const scopeSlugs =
        isGRI && griTopics.length > 1
          ? JSON.stringify(griTopics)
          : !isGRI && semiVals.length > 1
            ? JSON.stringify(semiVals)
            : undefined;
      const store = useFileStore.getState();
      batchMessageKey = `upload-batch-${Date.now()}`;
      void message.open({
        key: batchMessageKey,
        type: "loading",
        content: t("upload.uploadingBatch", { count: String(queuedFiles.length) }),
        duration: 0,
      });

      const nativeFiles = queuedFiles.map((uploadFile) => {
        const value = uploadFile.originFileObj ?? uploadFile;
        if (!(value instanceof File)) throw new Error(t("upload.invalidFile"));
        return value;
      });
      const companyId = values.companyId === "__new__" ? undefined : values.companyId;
      const displayName = uploadMode === "single"
        ? nativeFiles[0].name
        : companyId
          ? t("upload.companyBatch")
          : values.companyName || t("upload.companyBatch");
      const response = uploadMode === "single"
        ? await apiService.uploadReport(
            nativeFiles[0],
            values.framework,
            isCDP ? "CDP" : values.industry,
            isGRI ? "" : semiVals[0] ?? "",
            values.griSector,
            griTopics[0] ?? "",
            scopeSlugs,
          )
        : await apiService.uploadReportBatch(nativeFiles, {
            uploadMode,
            companyId,
            companyName: companyId ? undefined : values.companyName,
            reportYears: values.reportYears,
            framework: values.framework,
            industry: isCDP ? "CDP" : values.industry,
            semiIndustry: isGRI ? "" : semiVals[0] ?? "",
            griSector: values.griSector,
            griTopic: griTopics[0] ?? "",
            scopeSlugs,
          });
      const jobId = response.job_id;
      if (!jobId) throw new Error(t("upload.processingFailed"));

      message.destroy(batchMessageKey);
      setIsModalOpen(false);
      setSelectedUploadFiles([]);
      setSelectedIndustry("");
      form.resetFields();
      await store.loadFilesFromBackend({ forceFresh: true });

      const jobMessageKey = `report-job-${jobId}`;
      setActiveReportJobs((prev) => ({
        ...prev,
        [jobId]: {
          jobId,
          fileName: displayName,
          status: "processing",
          stage: "queued",
          message: response.message || t("upload.processingStarted"),
          progress: 0,
        },
      }));
      apiService.subscribeReportJob(jobId, {
        onEvent: (event) => {
          setActiveReportJobs((prev) => ({
            ...prev,
            [jobId]: {
              jobId,
              fileName: displayName,
              status: event.status,
              stage: event.stage,
              message: userFacingJobMessage(event),
              progress: event.progress,
              error: event.error,
            },
          }));
          void message.open({
            key: jobMessageKey,
            type: "loading",
            content: formatReportJobMessage(displayName, event),
            duration: 0,
          });
        },
        onDone: async (event) => {
          message.destroy(jobMessageKey);
          setActiveReportJobs((prev) => ({
            ...prev,
            [jobId]: {
              jobId,
              fileName: displayName,
              status: event.status,
              stage: "completed",
              message: userFacingJobMessage(event),
              progress: 100,
            },
          }));
          window.setTimeout(() => {
            setActiveReportJobs((prev) => {
              const next = { ...prev };
              delete next[jobId];
              return next;
            });
          }, 8000);
          void message.success(`${displayName}: ${event.message || t("upload.processingCompleted")}`);
          await store.loadFilesFromBackend({ forceFresh: true });
        },
        onError: async () => {
          message.destroy(jobMessageKey);
          const errorText = t("upload.processingFailed");
          setActiveReportJobs((prev) => ({
            ...prev,
            [jobId]: {
              ...(prev[jobId] || { jobId, fileName: displayName }),
              status: "failed",
              stage: "failed",
              message: errorText,
              error: errorText,
            },
          }));
          void message.error(`${displayName}: ${errorText}`);
          await store.loadFilesFromBackend({ forceFresh: true });
        },
      });
      void message.success(t("upload.batchQueued", { count: String(nativeFiles.length) }));
    } catch (error: any) {
      if (batchMessageKey) message.destroy(batchMessageKey);
      console.error("Validation failed:", error);
      void message.error(error?.message || t("upload.fillRequiredFields"));
    } finally {
      setUploadSubmitting(false);
    }
  };

  const handleModalCancel = () => {
    setIsModalOpen(false);
    setSelectedUploadFiles([]);
    setSelectedIndustry("");
    form.resetFields();
  };

  const activeJobList = Object.values(activeReportJobs);

  return (
    <>
      {activeJobList.length > 0 && (
        <div className="fixed bottom-4 right-4 z-[9999] w-[420px] max-w-[calc(100vw-2rem)] space-y-3">
          {activeJobList.map((job) => {
            const percent = Math.max(0, Math.min(100, Math.round(Number(job.progress ?? 0))));
            return (
              <div key={job.jobId} className="rounded-xl border border-slate-200 bg-white/95 p-4 shadow-xl backdrop-blur">
                <div className="mb-2 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-900">{job.fileName}</div>
                    <div className="mt-1 text-xs text-slate-600">{job.message || job.stage || "Processing"}</div>
                  </div>
                  <div className="shrink-0 text-xs font-medium text-slate-700">{percent}%</div>
                </div>
                <Progress percent={percent} size="small" status={job.status === "failed" ? "exception" : job.status === "success" || job.status === "partial_success" ? "success" : "active"} />
              </div>
            );
          })}
        </div>
      )}
      <div className="pt-3 sm:pt-4">
        <div
          className="grid grid-cols-1 items-stretch overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]"
          data-testid="upload-framework-layout"
        >
          <section className="flex h-full min-w-0 px-2 py-4 sm:px-3 sm:py-5" data-testid="upload-dropzone-region">
            <UploadArea
              onBeforeUpload={handleBeforeUpload}
              uploadMode={uploadMode}
            />
          </section>
        </div>
      </div>
      {isModalOpen ? (
        <UploadOptionsModal
          isOpen
          selectedUploadFiles={selectedUploadFiles}
          selectedIndustry={selectedIndustry}
          onOk={handleModalOk}
          onCancel={handleModalCancel}
          onIndustryChange={setSelectedIndustry}
          form={form}
          uploadMode={uploadMode}
          confirmLoading={uploadSubmitting}
        />
      ) : null}
    </>
  );
};

export default React.memo(MainContent);
