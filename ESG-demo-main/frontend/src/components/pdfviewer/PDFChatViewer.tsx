"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Button, InputNumber, Space, Typography } from "antd";
import { Minus, Plus, RotateCcw } from "lucide-react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { getStoredAuth } from "@/lib/auth";
import { useT } from "@/i18n/useT";

pdfjs.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.min.js";

const { Text } = Typography;

const MIN_ZOOM = 0.6;
const MAX_ZOOM = 2.6;
const ZOOM_STEP = 0.1;
const OVERSCAN_PAGES = 3;
const MAX_RENDERED_PAGES = 9;
const PAGE_GAP = 16;
const POINTER_DRAG_THRESHOLD = 3;

type PageSize = {
  width: number;
  height: number;
};

type ScrollAnchor = {
  page: number;
  offsetRatio: number;
};

type PointerDrag = {
  activated: boolean;
  element: HTMLDivElement;
  pointerId: number;
  startClientY: number;
  startScrollTop: number;
};

const DEFAULT_PAGE_SIZE: PageSize = { width: 612, height: 792 };

const POINTER_DRAG_EXCLUSION_SELECTOR = [
  "a",
  "button",
  "input",
  "textarea",
  "select",
  "option",
  "label",
  "[role='button']",
  "[role='link']",
  "[contenteditable='true']",
  ".annotationLayer section",
  ".annotationLayer [data-annotation-id]",
  ".react-pdf__Page__annotations [data-annotation-id]",
  ".textLayer span",
  ".react-pdf__Page__textContent span",
].join(",");

export type PDFChatViewerProps = {
  fileUrl: string;
  targetPage?: number;
  /** Re-run external navigation even when the requested page is unchanged. */
  targetPageNonce?: number;
  height?: string | number;
  defaultZoom?: number;
};

const clampZoom = (value: number) =>
  Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(value * 10) / 10));

const normalisePage = (value: unknown, totalPages?: number): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return null;
  const integer = Math.round(numeric);
  if (!totalPages) return Math.max(1, integer);
  return Math.min(totalPages, Math.max(1, integer));
};

const pageWindow = (focusPage: number, totalPages: number): Set<number> => {
  if (totalPages <= 0) return new Set();
  const focus = normalisePage(focusPage, totalPages) || 1;
  const start = Math.max(1, focus - OVERSCAN_PAGES);
  let end = Math.min(totalPages, focus + OVERSCAN_PAGES);

  if (end - start + 1 > MAX_RENDERED_PAGES) {
    end = start + MAX_RENDERED_PAGES - 1;
  }

  return new Set(Array.from({ length: end - start + 1 }, (_, index) => start + index));
};

const samePageSet = (left: Set<number>, right: Set<number>) => {
  if (left.size !== right.size) return false;
  for (const page of left) {
    if (!right.has(page)) return false;
  }
  return true;
};

const elementTopInScrollContainer = (
  container: HTMLElement,
  element: HTMLElement,
) => {
  const containerRect = container.getBoundingClientRect();
  const elementRect = element.getBoundingClientRect();
  return (
    container.scrollTop
    + elementRect.top
    - containerRect.top
    - container.clientTop
  );
};

export default function PDFChatViewer({
  fileUrl,
  targetPage = 1,
  targetPageNonce,
  height = "72vh",
  defaultZoom = 1,
}: PDFChatViewerProps) {
  const { t } = useT();
  const initialZoom = clampZoom(defaultZoom);

  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageDraft, setPageDraft] = useState<number | null>(1);
  const [zoom, setZoom] = useState(initialZoom);
  const [containerWidth, setContainerWidth] = useState(0);
  const [pageSizes, setPageSizes] = useState<Record<number, PageSize>>({});
  const [renderedPages, setRenderedPages] = useState<Set<number>>(new Set([1]));
  const [isPointerDragging, setIsPointerDragging] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const slotRefs = useRef(new Map<number, HTMLDivElement>());
  const visibleAreasRef = useRef(new Map<number, number>());
  const currentPageRef = useRef(1);
  const pageInputFocusedRef = useRef(false);
  const pendingAnchorRef = useRef<ScrollAnchor | null>(null);
  const documentRef = useRef<any>(null);
  const documentGenerationRef = useRef(0);
  const navigationRequestRef = useRef(0);
  const pageSizesRef = useRef<Record<number, PageSize>>({});
  const pointerDragRef = useRef<PointerDrag | null>(null);

  const pdfOptions = useMemo(() => {
    const token = getStoredAuth()?.token;
    return token ? { httpHeaders: { Authorization: `Bearer ${token}` } } : undefined;
  }, []);

  const captureScrollAnchor = useCallback((): ScrollAnchor | null => {
    const container = containerRef.current;
    const page = currentPageRef.current;
    const slot = slotRefs.current.get(page);
    if (!container || !slot || slot.offsetHeight <= 0) return null;

    const slotTop = elementTopInScrollContainer(container, slot);
    const offsetWithinPage = container.scrollTop - slotTop;
    return {
      page,
      offsetRatio: Math.min(1, Math.max(0, offsetWithinPage / slot.offsetHeight)),
    };
  }, []);

  useLayoutEffect(() => {
    const anchor = pendingAnchorRef.current;
    const container = containerRef.current;
    if (!anchor || !container) return;
    const slot = slotRefs.current.get(anchor.page);
    if (!slot) return;

    container.scrollTop = Math.max(
      0,
      elementTopInScrollContainer(container, slot)
        + slot.offsetHeight * anchor.offsetRatio,
    );
    pendingAnchorRef.current = null;
  }, [containerWidth, pageSizes, zoom]);

  const updatePageSize = useCallback(
    (pageNumber: number, size: PageSize) => {
      if (!(size.width > 0 && size.height > 0)) return;
      setPageSizes((previous) => {
        const existing = previous[pageNumber];
        if (
          existing &&
          Math.abs(existing.width - size.width) < 0.5 &&
          Math.abs(existing.height - size.height) < 0.5
        ) {
          return previous;
        }
        pendingAnchorRef.current = captureScrollAnchor();
        const next = { ...previous, [pageNumber]: size };
        pageSizesRef.current = next;
        return next;
      });
    },
    [captureScrollAnchor],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const measure = () => {
      const nextWidth = Math.max(0, container.clientWidth - 16);
      setContainerWidth((previous) => {
        if (Math.abs(previous - nextWidth) < 1) return previous;
        if (previous > 0) pendingAnchorRef.current = captureScrollAnchor();
        return nextWidth;
      });
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, [captureScrollAnchor]);

  const setZoomWithAnchor = useCallback(
    (updater: (previous: number) => number) => {
      setZoom((previous) => {
        const next = clampZoom(updater(previous));
        if (next === previous) return previous;
        pendingAnchorRef.current = captureScrollAnchor();
        return next;
      });
    },
    [captureScrollAnchor],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const onWheel = (event: WheelEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      const direction = event.deltaY > 0 ? -1 : 1;
      setZoomWithAnchor((previous) => previous + direction * ZOOM_STEP);
    };

    container.addEventListener("wheel", onWheel, { passive: false });
    return () => container.removeEventListener("wheel", onWheel);
  }, [setZoomWithAnchor]);

  const beginPointerDrag = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (
        event.button !== 0 ||
        event.isPrimary === false ||
        event.pointerType !== "mouse"
      ) {
        return;
      }

      const target = event.target;
      if (
        target instanceof Element &&
        target.closest(POINTER_DRAG_EXCLUSION_SELECTOR)
      ) {
        return;
      }

      pointerDragRef.current = {
        activated: false,
        element: event.currentTarget,
        pointerId: event.pointerId,
        startClientY: event.clientY,
        startScrollTop: event.currentTarget.scrollTop,
      };
      event.currentTarget.setPointerCapture?.(event.pointerId);
    },
    [],
  );

  const movePointerDrag = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const drag = pointerDragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;

      const deltaY = event.clientY - drag.startClientY;
      if (Math.abs(deltaY) < POINTER_DRAG_THRESHOLD) return;

      if (!drag.activated) {
        drag.activated = true;
        pendingAnchorRef.current = null;
        setIsPointerDragging(true);
      }
      event.preventDefault();
      event.currentTarget.scrollTop = Math.max(
        0,
        drag.startScrollTop - deltaY,
      );
    },
    [],
  );

  const cancelPointerDrag = useCallback((updateUi = true) => {
    const drag = pointerDragRef.current;
    if (!drag) return;

    pointerDragRef.current = null;
    if (drag.element.hasPointerCapture?.(drag.pointerId)) {
      drag.element.releasePointerCapture?.(drag.pointerId);
    }
    if (updateUi) setIsPointerDragging(false);
  }, []);

  const endPointerDrag = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const drag = pointerDragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      cancelPointerDrag();
    },
    [cancelPointerDrag],
  );

  useEffect(() => {
    const onWindowBlur = () => cancelPointerDrag();
    window.addEventListener("blur", onWindowBlur);
    return () => {
      window.removeEventListener("blur", onWindowBlur);
      cancelPointerDrag(false);
    };
  }, [cancelPointerDrag]);

  const applyVisiblePages = useCallback(
    (entries: IntersectionObserverEntry[]) => {
      for (const entry of entries) {
        const page = Number((entry.target as HTMLElement).dataset.pageNumber);
        if (!Number.isFinite(page)) continue;
        if (!entry.isIntersecting) {
          visibleAreasRef.current.delete(page);
          continue;
        }
        const area = Math.max(0, entry.intersectionRect.width * entry.intersectionRect.height);
        visibleAreasRef.current.set(page, area || entry.intersectionRatio);
      }

      const visible = [...visibleAreasRef.current.entries()]
        .filter(([, area]) => area > 0)
        .sort((left, right) => right[1] - left[1]);
      if (!visible.length) return;

      const nextCurrentPage = visible[0][0];
      if (nextCurrentPage !== currentPageRef.current) {
        currentPageRef.current = nextCurrentPage;
        setCurrentPage(nextCurrentPage);
        if (!pageInputFocusedRef.current) setPageDraft(nextCurrentPage);
      }

      const nextWindow = pageWindow(nextCurrentPage, numPages);
      setRenderedPages((previous) =>
        samePageSet(previous, nextWindow) ? previous : nextWindow,
      );
    },
    [numPages],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !numPages || typeof IntersectionObserver === "undefined") return;
    const visibleAreas = visibleAreasRef.current;

    const observer = new IntersectionObserver(applyVisiblePages, {
      root: container,
      threshold: [0, 0.01, 0.1, 0.25, 0.5, 0.75, 1],
    });
    slotRefs.current.forEach((slot) => observer.observe(slot));
    return () => {
      observer.disconnect();
      visibleAreas.clear();
    };
  }, [applyVisiblePages, numPages]);

  const scrollToPage = useCallback(
    async (requestedPage: unknown) => {
      if (!numPages) return;
      cancelPointerDrag();
      const page = normalisePage(requestedPage, numPages);
      if (!page) return;

      const navigationRequest = ++navigationRequestRef.current;
      const generation = documentGenerationRef.current;
      const pdfDocument = documentRef.current;
      if (!pageSizesRef.current[page] && pdfDocument?.getPage) {
        try {
          const pdfPage = await pdfDocument.getPage(page);
          if (generation === documentGenerationRef.current) {
            const viewport = pdfPage.getViewport({ scale: 1 });
            updatePageSize(page, { width: viewport.width, height: viewport.height });
          }
        } catch {
          // The Page component will surface a page-level rendering error.
        }
      }

      if (
        generation !== documentGenerationRef.current
        || navigationRequest !== navigationRequestRef.current
      ) return;
      currentPageRef.current = page;
      setCurrentPage(page);
      setPageDraft(page);
      setRenderedPages(pageWindow(page, numPages));

      window.requestAnimationFrame(() => {
        if (navigationRequest !== navigationRequestRef.current) return;
        window.requestAnimationFrame(() => {
          if (navigationRequest !== navigationRequestRef.current) return;
          const container = containerRef.current;
          const slot = slotRefs.current.get(page);
          if (!container || !slot) return;
          container.scrollTo({
            top: Math.max(0, elementTopInScrollContainer(container, slot) - 8),
            behavior: "auto",
          });
        });
      });
    },
    [cancelPointerDrag, numPages, updatePageSize],
  );

  useEffect(() => {
    if (!numPages) return;
    void scrollToPage(targetPage);
  }, [numPages, scrollToPage, targetPage, targetPageNonce]);

  useEffect(() => {
    documentGenerationRef.current += 1;
    navigationRequestRef.current += 1;
    documentRef.current = null;
    visibleAreasRef.current.clear();
    pendingAnchorRef.current = null;
    currentPageRef.current = 1;
    setNumPages(0);
    setCurrentPage(1);
    setPageDraft(1);
    setZoom(clampZoom(defaultZoom));
    pageSizesRef.current = {};
    setPageSizes({});
    setRenderedPages(new Set([1]));
    cancelPointerDrag();
    const container = containerRef.current;
    if (container) container.scrollTop = 0;
  }, [cancelPointerDrag, defaultZoom, fileUrl]);

  const preloadPageSizes = useCallback(
    async (pdfDocument: any, totalPages: number, generation: number) => {
      const sizes: Record<number, PageSize> = {};
      let cursor = 1;

      const worker = async () => {
        while (cursor <= totalPages) {
          const pageNumber = cursor;
          cursor += 1;
          try {
            const pdfPage = await pdfDocument.getPage(pageNumber);
            const viewport = pdfPage.getViewport({ scale: 1 });
            sizes[pageNumber] = { width: viewport.width, height: viewport.height };
          } catch {
            sizes[pageNumber] = DEFAULT_PAGE_SIZE;
          }
        }
      };

      await Promise.all(Array.from({ length: Math.min(4, totalPages) }, worker));
      if (generation !== documentGenerationRef.current) return;
      pendingAnchorRef.current = captureScrollAnchor();
      pageSizesRef.current = sizes;
      setPageSizes(sizes);
    },
    [captureScrollAnchor],
  );

  const onDocumentLoadSuccess = useCallback(
    (pdfDocument: any) => {
      const totalPages = Math.max(0, Number(pdfDocument?.numPages) || 0);
      documentRef.current = pdfDocument;
      const generation = documentGenerationRef.current;
      const requestedPage = normalisePage(targetPage, totalPages) || 1;

      currentPageRef.current = requestedPage;
      setNumPages(totalPages);
      setCurrentPage(requestedPage);
      setPageDraft(requestedPage);
      setRenderedPages(pageWindow(requestedPage, totalPages));
      void preloadPageSizes(pdfDocument, totalPages, generation);
    },
    [preloadPageSizes, targetPage],
  );

  const commitPageDraft = useCallback((requestedPage: unknown = pageDraft) => {
    pageInputFocusedRef.current = false;
    const page = normalisePage(requestedPage, numPages);
    if (!page) {
      setPageDraft(currentPageRef.current);
      return;
    }
    setPageDraft(page);
    void scrollToPage(page);
  }, [numPages, pageDraft, scrollToPage]);

  const fittedPageWidth = Math.max(1, containerWidth || 1);
  const pageNumbers = useMemo(
    () => Array.from({ length: numPages }, (_, index) => index + 1),
    [numPages],
  );

  return (
    <div
      role="region"
      aria-label="PDF document viewer"
      className="flex h-full w-full flex-col"
      style={{ height }}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-2 py-2">
        <Space size={6} wrap>
          <Text style={{ fontSize: 12, opacity: 0.75 }}>{t("common.page")}</Text>
          <InputNumber
            aria-label="Page number"
            size="small"
            min={1}
            max={numPages || 1}
            precision={0}
            value={pageDraft}
            onFocus={() => {
              pageInputFocusedRef.current = true;
            }}
            onChange={(value) => setPageDraft(typeof value === "number" ? value : null)}
            onBlur={(event) => commitPageDraft(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                commitPageDraft(event.currentTarget.value);
              }
            }}
            style={{ width: 82 }}
          />
          <Text style={{ fontSize: 12, opacity: 0.65 }}>/ {numPages || "—"}</Text>
        </Space>

        <Space size={6} wrap>
          <Button
            aria-label="Zoom out"
            size="small"
            onClick={() => setZoomWithAnchor((previous) => previous - ZOOM_STEP)}
            icon={<Minus size={16} />}
            disabled={zoom <= MIN_ZOOM}
          />
          <Text style={{ minWidth: 42, textAlign: "center", fontSize: 12, opacity: 0.75 }}>
            {Math.round(zoom * 100)}%
          </Text>
          <Button
            aria-label="Zoom in"
            size="small"
            onClick={() => setZoomWithAnchor((previous) => previous + ZOOM_STEP)}
            icon={<Plus size={16} />}
            disabled={zoom >= MAX_ZOOM}
          />
          <Button
            aria-label="Reset zoom"
            size="small"
            onClick={() => setZoomWithAnchor(() => initialZoom)}
            icon={<RotateCcw size={16} />}
          />
        </Space>
      </div>

      <div
        ref={containerRef}
        data-testid="pdf-scroll-container"
        className={`min-h-0 flex-1 overflow-auto rounded-b-lg bg-gray-100 p-2 ${
          isPointerDragging ? "cursor-grabbing select-none" : "cursor-grab"
        }`}
        style={{
          // Keep wide, zoomed pages horizontally contained, but let a vertical
          // wheel/touch gesture continue onto the Compliance page at PDF bounds.
          overscrollBehaviorX: "contain",
          overscrollBehaviorY: "auto",
          WebkitOverflowScrolling: "touch",
        }}
        onPointerDown={beginPointerDrag}
        onPointerMove={movePointerDrag}
        onPointerUp={endPointerDrag}
        onPointerCancel={endPointerDrag}
        onLostPointerCapture={endPointerDrag}
      >
        <Document
          key={fileUrl}
          file={fileUrl}
          options={pdfOptions}
          onLoadSuccess={onDocumentLoadSuccess}
          loading={
            <div role="status" className="p-4 text-center text-sm text-gray-500">
              {t("common.loading")}
            </div>
          }
          error={
            <div role="alert" className="p-4 text-center text-sm text-red-600">
              {t("common.failedToLoadPdf")}
            </div>
          }
        >
          <div
            className="mx-auto flex min-w-full flex-col items-center"
            style={{ width: Math.max(fittedPageWidth, fittedPageWidth * zoom) }}
          >
            {pageNumbers.map((pageNumber) => {
              const size = pageSizes[pageNumber] || pageSizes[1] || DEFAULT_PAGE_SIZE;
              const width = fittedPageWidth * zoom;
              const pageHeight = width * (size.height / size.width);
              const shouldRender = renderedPages.has(pageNumber);

              return (
                <div
                  key={pageNumber}
                  ref={(element) => {
                    if (element) slotRefs.current.set(pageNumber, element);
                    else slotRefs.current.delete(pageNumber);
                  }}
                  data-page-number={pageNumber}
                  className="relative shrink-0 overflow-hidden bg-white shadow-sm"
                  style={{
                    width,
                    height: pageHeight,
                    marginBottom: pageNumber === numPages ? 0 : PAGE_GAP,
                  }}
                >
                  {shouldRender ? (
                    <div data-rendered="true" className="h-full w-full">
                      <Page
                        pageNumber={pageNumber}
                        width={width}
                        renderTextLayer
                        renderAnnotationLayer
                        loading={null}
                        onLoadSuccess={(pdfPage: any) => {
                          const viewport = pdfPage.getViewport({ scale: 1 });
                          updatePageSize(pageNumber, {
                            width: viewport.width,
                            height: viewport.height,
                          });
                        }}
                        error={
                          <div role="alert" className="flex h-full items-center justify-center p-4 text-sm text-red-600">
                            {t("common.failedToLoadPdf")}
                          </div>
                        }
                      />
                    </div>
                  ) : (
                    <div aria-hidden="true" className="h-full w-full bg-white" />
                  )}
                </div>
              );
            })}
          </div>
        </Document>
      </div>

      <div className="pt-2 text-center text-xs text-gray-500" aria-live="polite">
        {t("common.page")} {currentPage} / {numPages || "—"}
      </div>
    </div>
  );
}
