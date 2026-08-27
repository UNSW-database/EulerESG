"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { Button, InputNumber, Space, Typography } from "antd";
import { Minus, Plus, RotateCcw, ChevronLeft, ChevronRight } from "lucide-react";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { getStoredAuth } from "@/lib/auth";
import { useT } from "@/i18n/useT";

// pdf.js worker
pdfjs.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.min.js";

const { Text } = Typography;

export type PDFEvidenceViewerProps = {
  fileUrl: string;
  initialPage?: number;
  /** Re-run external page navigation when the same page is requested twice. */
  navigationNonce?: number;
  height?: string | number;
  /**
   * Default zoom multiplier (relative to the chosen fit mode).
   * - 1.0 means "fit" (width or page)
   */
  defaultZoom?: number;
  /**
   * Fit mode:
   * - "width": fit page width (common reading)
   * - "page": fit whole page into the viewport (no scrolling by default)
   */
  fitTo?: "width" | "page";
  /**
   * Scrolling mode:
   * - "container": the PDF sits in an internal scroll container (legacy)
   * - "page": use the browser/page scroll only (no nested scroll frame)
   */
  scrollMode?: "container" | "page";
};

type PageSize = { w: number; h: number } | null;

export default function PDFEvidenceViewer({
  fileUrl,
  initialPage = 1,
  navigationNonce,
  height = "72vh",
  defaultZoom = 1.15,
  fitTo = "width",
  scrollMode = "container",
}: PDFEvidenceViewerProps) {
  const { t } = useT();

  const [numPages, setNumPages] = useState<number>(0);
  const [page, setPage] = useState<number>(Math.max(1, initialPage));
  const [zoom, setZoom] = useState<number>(Math.min(2.6, Math.max(0.6, defaultZoom)));
  const [containerWidth, setContainerWidth] = useState<number>(0);
  const [containerHeight, setContainerHeight] = useState<number>(0);
  const [pageSize, setPageSize] = useState<PageSize>(null);
  const [hovered, setHovered] = useState<boolean>(false);

  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setPage(Math.max(1, initialPage));
  }, [initialPage, navigationNonce]);

  // Pass auth header for protected PDF endpoint
  const pdfOptions = useMemo(() => {
    const auth = getStoredAuth();
    const token = auth?.token;
    if (!token) return undefined;
    return { httpHeaders: { Authorization: `Bearer ${token}` } };
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      setContainerWidth(el.clientWidth);
      setContainerHeight(el.clientHeight);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Ctrl/Cmd + wheel zoom (avoid browser zoom while cursor is over viewer)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      // Prevent browser zoom.
      e.preventDefault();
      const dir = e.deltaY > 0 ? -1 : 1;
      setZoom((s) => {
        const next = Math.round((s + dir * 0.1) * 10) / 10;
        return Math.min(2.6, Math.max(0.6, next));
      });
    };

    // Must be non-passive to preventDefault.
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel as any);
  }, []);

  // Robust Ctrl/Cmd + wheel zoom: also capture at window level so the browser doesn't zoom
  // when the PDF canvas/text layers swallow the wheel event.
  useEffect(() => {
    const onWheelCapture = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const el = containerRef.current;
      if (!el) return;
      // Only intercept when the pointer is over the viewer.
      if (!el.contains(e.target as Node)) return;
      e.preventDefault();
      const dir = e.deltaY > 0 ? -1 : 1;
      setZoom((s) => {
        const next = Math.round((s + dir * 0.1) * 10) / 10;
        return Math.min(2.6, Math.max(0.6, next));
      });
    };
    window.addEventListener("wheel", onWheelCapture, { passive: false, capture: true });
    return () => window.removeEventListener("wheel", onWheelCapture as any, true);
  }, []);

  // Ctrl/Cmd + / - / 0 zoom shortcuts
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      // Only apply viewer zoom shortcuts when the discourser is over the viewer,
      // so we don't hijack global shortcuts elsewhere in the app.
      if (!hovered) return;
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key === "+" || e.key === "=") {
        e.preventDefault();
        setZoom((s) => Math.min(2.6, Math.round((s + 0.1) * 10) / 10));
        return;
      }
      if (e.key === "-" || e.key === "_") {
        e.preventDefault();
        setZoom((s) => Math.max(0.6, Math.round((s - 0.1) * 10) / 10));
        return;
      }
      if (e.key === "0") {
        e.preventDefault();
        setZoom(Math.min(2.6, Math.max(0.6, defaultZoom)));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [defaultZoom, hovered]);

  const clampPage = (p: number) => {
    if (!numPages) return Math.max(1, p);
    return Math.min(Math.max(1, p), numPages);
  };

  const zoomIn = () => setZoom((s) => Math.min(2.6, Math.round((s + 0.1) * 10) / 10));
  const zoomOut = () => setZoom((s) => Math.max(0.6, Math.round((s - 0.1) * 10) / 10));
  const resetZoom = () => setZoom(Math.min(2.6, Math.max(0.6, defaultZoom)));

  const onPageLoadSuccess = useCallback((p: any) => {
    try {
      const vp = p.getViewport({ scale: 1 });
      if (vp?.width && vp?.height) setPageSize({ w: vp.width, h: vp.height });
    } catch {
      // ignore
    }
  }, []);

  const pageScale = useMemo(() => {
    if (!containerWidth || !pageSize) return undefined;

    // Gutter keeps page from touching the edges.
    const gutter = scrollMode === "page" ? 56 : 20;
    const wScale = Math.max(0.1, (containerWidth - gutter) / pageSize.w);

    // In container mode we can optionally fit to the viewport height as well.
    if (scrollMode === "container") {
      if (!containerHeight) return undefined;
      const hScale = Math.max(0.1, (containerHeight - gutter) / pageSize.h);
      const base = fitTo === "page" ? Math.min(wScale, hScale) : wScale;
      return Math.max(0.1, base) * zoom;
    }

    // In page mode, fit-to-width only; let the document flow naturally.
    const base = wScale;
    return Math.max(0.1, base) * zoom;
  }, [containerWidth, containerHeight, pageSize, fitTo, zoom, scrollMode]);

  const overflowMode = useMemo(() => {
    if (scrollMode === "page") return "visible";
    // Fit-to-page aims for "no scroll" at zoom=1; allow scroll once user zooms in.
    if (fitTo === "page") return zoom <= 1.001 ? "hidden" : "auto";
    return "auto";
  }, [fitTo, zoom, scrollMode]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        // In page mode, avoid forcing a fixed height so the browser scroll is used.
        height: scrollMode === "container" ? "100%" : "auto",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          padding: "8px 10px",
        }}
      >
        <Space size={6} wrap>
          <Button
            size="small"
            onClick={() => setPage((p) => clampPage(p - 1))}
            icon={<ChevronLeft size={16} />}
            disabled={page <= 1}
          />
          <Button
            size="small"
            onClick={() => setPage((p) => clampPage(p + 1))}
            icon={<ChevronRight size={16} />}
            disabled={!!numPages && page >= numPages}
          />
          <Text style={{ fontSize: 12, opacity: 0.75 }}>{t("common.page")}</Text>
          <InputNumber
            size="small"
            min={1}
            max={numPages || 9999}
            value={page}
            onChange={(v) => setPage(clampPage(typeof v === "number" ? v : page))}
            style={{ width: 88 }}
          />
          <Text style={{ fontSize: 12, opacity: 0.65 }}>/ {numPages || "—"}</Text>
        </Space>

        <Space size={6} wrap>
          <Button size="small" onClick={zoomOut} icon={<Minus size={16} />} />
          <Text style={{ fontSize: 12, opacity: 0.75 }}>{Math.round(zoom * 100)}%</Text>
          <Button size="small" onClick={zoomIn} icon={<Plus size={16} />} />
          <Button size="small" onClick={resetZoom} icon={<RotateCcw size={16} />} />
        </Space>
      </div>

      <div
        ref={containerRef}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          flex: scrollMode === "container" ? 1 : "unset",
          overflow: overflowMode as any,
          overscrollBehaviorY: "auto",
          WebkitOverflowScrolling: "touch",
          height: scrollMode === "container" ? height : "auto",
          padding: 8,
          background: "rgba(255,255,255,0.7)",
          borderRadius: 12,
          display: scrollMode === "container" ? "flex" : "block",
          ...(scrollMode === "container"
            ? { alignItems: "center", justifyContent: "center" }
            : {}),
          // Ensure large zoom doesn't get clipped.
          width: "100%",
        }}
      >
        <Document
          file={fileUrl}
          options={pdfOptions}
          onLoadSuccess={({ numPages: n }) => setNumPages(n)}
          loading={<div style={{ padding: 16, opacity: 0.7 }}>{t("common.loading")}</div>}
          error={<div style={{ padding: 16 }}>{t("common.failedToLoadPdf")}</div>}
        >
          <div style={{ width: "100%", display: "flex", justifyContent: "center" }}>
            <Page
              pageNumber={clampPage(page)}
              scale={pageScale}
              onLoadSuccess={onPageLoadSuccess}
              renderTextLayer
              renderAnnotationLayer
            />
          </div>
        </Document>
      </div>
    </div>
  );
}
