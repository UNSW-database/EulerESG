"use client";
import dynamic from "next/dynamic";

function DashboardWorkspaceLoading() {
  return (
    <main
      data-testid="dashboard-workspace-loading"
      aria-busy="true"
      aria-label="Loading reports"
      className="min-h-screen w-full bg-slate-50 px-6 py-5"
    >
      <div className="mx-auto w-[95%] animate-pulse space-y-4">
        <div className="h-56 rounded-2xl border border-slate-200 bg-white" />
        <div className="h-12 rounded-xl border border-slate-200 bg-white" />
        <div className="h-80 rounded-2xl border border-slate-200 bg-white" />
      </div>
    </main>
  );
}

const PDFViewer = dynamic(
  () => import("@/components/pdfviewer/PDFViewer"),
  { ssr: false, loading: DashboardWorkspaceLoading },
);

const FloatingStatusButton = dynamic(
  () => import("@/components/status/FloatingStatusButton"),
  { ssr: false },
);

const SHOW_DEV_TOOLS = /^(1|true|yes|on)$/i.test(
  process.env.NEXT_PUBLIC_SHOW_DEV_TOOLS?.trim() ?? "",
);

export default function DashboardPage() {
  return (
    <>
      <PDFViewer />
      {SHOW_DEV_TOOLS && <FloatingStatusButton />}
    </>
  );
}
