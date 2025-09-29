"use client";
import PDFViewer from "@/components/pdfviewer/PDFViewer";
import FloatingStatusButton from "@/components/status/FloatingStatusButton";

export default function DashboardPage() {
  return (
    <>
      <PDFViewer />
      <FloatingStatusButton />
    </>
  );
}
