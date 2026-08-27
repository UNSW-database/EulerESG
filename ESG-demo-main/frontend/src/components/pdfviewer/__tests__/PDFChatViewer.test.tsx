import type { ReactNode } from "react";

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PDFChatViewer from "../PDFChatViewer";
import { MockIntersectionObserver } from "@/test/setup";

vi.mock("@/lib/auth", () => ({
  getStoredAuth: () => null,
}));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({
    t: (key: string) =>
      ({
        "common.error": "Failed to load PDF.",
        "common.failedToLoadPdf": "Failed to load PDF.",
        "common.loading": "Loading PDF...",
        "common.page": "Page",
      })[key] ?? key,
  }),
}));

vi.mock("react-pdf", async () => {
  const React = await import("react");

  type DocumentProps = {
    children?: ReactNode;
    error?: ReactNode;
    file?: unknown;
    loading?: ReactNode;
    onLoadError?: (error: Error) => void;
    onLoadSuccess?: (document: { numPages: number }) => void;
  };

  type PageProps = {
    onLoadSuccess?: (page: {
      getViewport: (options?: { scale?: number }) => {
        height: number;
        width: number;
      };
      height: number;
      width: number;
    }) => void;
    onRenderSuccess?: () => void;
    pageNumber: number;
    scale?: number;
    width?: number;
  };

  const sourceName = (file: unknown): string => {
    if (typeof file === "string") return file;
    if (file && typeof file === "object" && "url" in file) {
      return String((file as { url: unknown }).url);
    }
    return "";
  };

  const mockPdfPage = (pageNumber: number) => ({
    getViewport: ({ scale = 1 }: { scale?: number } = {}) => ({
      height: (pageNumber % 5 === 0 ? 612 : 792) * scale,
      width: (pageNumber % 5 === 0 ? 792 : 612) * scale,
    }),
  });

  const Document = ({
    children,
    error,
    file,
    loading,
    onLoadError,
    onLoadSuccess,
  }: DocumentProps) => {
    const source = sourceName(file);
    const isLoading = source.includes("loading");
    const isError = source.includes("error");

    React.useEffect(() => {
      let cancelled = false;
      if (isLoading) return;
      if (isError) {
        queueMicrotask(() => {
          if (!cancelled) onLoadError?.(new Error("mock PDF load failure"));
        });
      } else {
        const numPages = source.includes("ten-pages") ? 10 : 116;
        const pdfDocument = {
          getPage: vi.fn(async (pageNumber: number) => {
            if (source.includes("navigation-race") && pageNumber === 20) {
              await new Promise((resolve) => window.setTimeout(resolve, 40));
            }
            return mockPdfPage(pageNumber);
          }),
          numPages,
        };
        queueMicrotask(() => {
          if (!cancelled) onLoadSuccess?.(pdfDocument);
        });
      }

      return () => {
        cancelled = true;
      };
      // The source is the mock's document identity. Callback identities from the
      // viewer are intentionally excluded to match pdf.js firing once per load.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [source]);

    if (isLoading) {
      return (
        <div data-testid="mock-pdf-loading">
          {loading ?? <div role="status">Loading PDF...</div>}
        </div>
      );
    }

    if (isError) {
      return (
        <div data-testid="mock-pdf-error">
          {error ?? <div role="alert">Failed to load PDF.</div>}
        </div>
      );
    }

    return (
      <div data-file={source} data-testid="mock-pdf-document">
        {children}
      </div>
    );
  };

  const Page = ({
    onLoadSuccess,
    onRenderSuccess,
    pageNumber,
    scale,
    width,
  }: PageProps) => {
    React.useEffect(() => {
      const baseWidth = 612;
      const baseHeight = 792;
      onLoadSuccess?.({
        getViewport: ({ scale: viewportScale = 1 } = {}) => ({
          height: baseHeight * viewportScale,
          width: baseWidth * viewportScale,
        }),
        height: baseHeight,
        width: baseWidth,
      });
      onRenderSuccess?.();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pageNumber]);

    return (
      <div
        className="react-pdf__Page"
        data-mock-page-number={pageNumber}
        data-mock-scale={scale ?? ""}
        data-mock-width={width ?? ""}>
        <canvas data-testid={`mock-canvas-${pageNumber}`} />
        <div
          className="react-pdf__Page__textContent textLayer"
          data-testid={`mock-text-layer-${pageNumber}`}>
          <span data-testid={`mock-text-${pageNumber}`}>Mock page {pageNumber}</span>
        </div>
        <div className="react-pdf__Page__annotations annotationLayer">
          <a data-testid={`mock-link-${pageNumber}`} href={`#page-${pageNumber}`}>
            Page link {pageNumber}
          </a>
          <input
            aria-label={`Mock field ${pageNumber}`}
            data-testid={`mock-input-${pageNumber}`}
          />
        </div>
      </div>
    );
  };

  return {
    Document,
    Page,
    pdfjs: {
      GlobalWorkerOptions: {},
    },
  };
});

const renderedPages = (): HTMLElement[] =>
  Array.from(document.querySelectorAll<HTMLElement>(".react-pdf__Page"));

const renderedPageNumbers = (): number[] =>
  renderedPages().map((page) => Number(page.dataset.mockPageNumber));

const pageSlots = (): HTMLElement[] =>
  Array.from(document.querySelectorAll<HTMLElement>("[data-page-number]"));

const pageSlot = (pageNumber: number): HTMLElement | null =>
  document.querySelector<HTMLElement>(`[data-page-number="${pageNumber}"]`);

const expectDocumentReady = async (numPages: number): Promise<void> => {
  await waitFor(() => {
    expect(pageSlots()).toHaveLength(numPages);
  });
};

const expectCurrentPage = async (
  pageNumber: number,
  numPages: number,
): Promise<void> => {
  await waitFor(() => {
    expect(
      screen.getByText(
        new RegExp(`Page\\s+${pageNumber}\\s*\\/\\s*${numPages}`),
      ),
    ).toBeInTheDocument();
  });
};

const mockPointerCapture = (element: HTMLElement) => {
  const capturedPointers = new Set<number>();
  const setPointerCapture = vi.fn((pointerId: number) => {
    capturedPointers.add(pointerId);
  });
  const releasePointerCapture = vi.fn((pointerId: number) => {
    capturedPointers.delete(pointerId);
  });
  const hasPointerCapture = vi.fn((pointerId: number) =>
    capturedPointers.has(pointerId),
  );

  Object.defineProperties(element, {
    hasPointerCapture: {
      configurable: true,
      value: hasPointerCapture,
    },
    releasePointerCapture: {
      configurable: true,
      value: releasePointerCapture,
    },
    setPointerCapture: {
      configurable: true,
      value: setPointerCapture,
    },
  });

  return { hasPointerCapture, releasePointerCapture, setPointerCapture };
};

type MockPointerEventInit = MouseEventInit & {
  isPrimary?: boolean;
  pointerId?: number;
  pointerType?: string;
};

const dispatchPointerEvent = (
  target: Node,
  type: string,
  init: MockPointerEventInit,
): MouseEvent => {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    ...init,
  });
  Object.defineProperties(event, {
    isPrimary: {
      configurable: true,
      value: init.isPrimary ?? true,
    },
    pointerId: {
      configurable: true,
      value: init.pointerId ?? 1,
    },
    pointerType: {
      configurable: true,
      value: init.pointerType ?? "mouse",
    },
  });
  fireEvent(target, event);
  return event;
};

describe("PDFChatViewer continuous rendering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps a 116-page document continuous while mounting no more than nine heavy pages", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);

    await expectDocumentReady(116);

    expect(screen.getByRole("region")).toBeInTheDocument();
    expect(screen.getByTestId("pdf-scroll-container")).toBeInTheDocument();
    expect(renderedPages().length).toBeGreaterThan(0);
    expect(renderedPages().length).toBeLessThanOrEqual(9);
    expect(renderedPageNumbers()).toContain(1);

    for (const renderedPage of renderedPages()) {
      const renderedBoundary = renderedPage.closest<HTMLElement>(
        '[data-rendered="true"]',
      );
      expect(renderedBoundary).not.toBeNull();
    }
  });

  it("lazily swaps rendered pages when a later page enters the viewport", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const thirtiethSlot = pageSlot(30);
    expect(thirtiethSlot).not.toBeNull();

    await waitFor(() => {
      expect(
        MockIntersectionObserver.instances.some((observer) =>
          observer.targets.has(thirtiethSlot!),
        ),
      ).toBe(true);
    });

    let wasObserved = false;
    act(() => {
      wasObserved = MockIntersectionObserver.trigger(thirtiethSlot!, {
        intersectionRatio: 1,
        isIntersecting: true,
      });
    });

    expect(wasObserved).toBe(true);
    await waitFor(() => {
      expect(renderedPageNumbers()).toContain(30);
    });
    expect(renderedPages().length).toBeLessThanOrEqual(9);
  });

  it("clamps target pages and repeats a same-page jump when the nonce changes", async () => {
    const view = render(
      <PDFChatViewer
        fileUrl="report-116.pdf"
        targetPage={999}
        targetPageNonce={1}
      />,
    );

    await expectDocumentReady(116);
    await expectCurrentPage(116, 116);
    await waitFor(() => {
      expect(renderedPageNumbers()).toContain(116);
    });

    const scrollTo = vi.mocked(HTMLElement.prototype.scrollTo);
    await waitFor(() => {
      expect(scrollTo).toHaveBeenCalled();
    });
    const firstJumpCallCount = scrollTo.mock.calls.length;

    view.rerender(
      <PDFChatViewer
        fileUrl="report-116.pdf"
        targetPage={999}
        targetPageNonce={2}
      />,
    );

    await waitFor(() => {
      expect(scrollTo.mock.calls.length).toBeGreaterThan(firstJumpCallCount);
    });

    view.rerender(
      <PDFChatViewer
        fileUrl="report-116.pdf"
        targetPage={0}
        targetPageNonce={3}
      />,
    );
    await expectCurrentPage(1, 116);
    await waitFor(() => {
      expect(renderedPageNumbers()).toContain(1);
    });
  });

  it("uses scroll-container coordinates instead of document-relative offsetTop", async () => {
    const view = render(
      <PDFChatViewer
        fileUrl="report-116.pdf"
        targetPage={5}
        targetPageNonce={1}
      />,
    );
    await expectDocumentReady(116);

    const container = screen.getByTestId("pdf-scroll-container");
    const slot = pageSlot(5);
    expect(slot).not.toBeNull();
    container.scrollTop = 250;
    vi.spyOn(container, "getBoundingClientRect").mockReturnValue({
      top: 500,
    } as DOMRect);
    vi.spyOn(slot!, "getBoundingClientRect").mockReturnValue({
      top: 4314,
    } as DOMRect);
    Object.defineProperty(slot!, "offsetTop", {
      configurable: true,
      value: 9000,
    });

    const scrollTo = vi.mocked(HTMLElement.prototype.scrollTo);
    scrollTo.mockClear();
    view.rerender(
      <PDFChatViewer
        fileUrl="report-116.pdf"
        targetPage={5}
        targetPageNonce={2}
      />,
    );

    await waitFor(() => {
      expect(scrollTo).toHaveBeenLastCalledWith({
        top: 4056,
        behavior: "auto",
      });
    });
  });

  it("keeps the latest page when an older navigation request finishes later", async () => {
    const view = render(
      <PDFChatViewer
        fileUrl="navigation-race.pdf"
        targetPage={20}
        targetPageNonce={1}
      />,
    );
    await expectDocumentReady(116);

    view.rerender(
      <PDFChatViewer
        fileUrl="navigation-race.pdf"
        targetPage={30}
        targetPageNonce={2}
      />,
    );
    await expectCurrentPage(30, 116);

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 70));
    });
    await expectCurrentPage(30, 116);
  });

  it("commits direct page input on Enter and clamps it to the document", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const pageInput = screen.getByRole("spinbutton", { name: "Page number" });
    fireEvent.focus(pageInput);
    fireEvent.change(pageInput, { target: { value: "48" } });
    fireEvent.keyDown(pageInput, { key: "Enter" });

    await expectCurrentPage(48, 116);
    await waitFor(() => {
      expect(renderedPageNumbers()).toContain(48);
    });

    const updatedPageInput = screen.getByRole("spinbutton", { name: "Page number" });
    fireEvent.focus(updatedPageInput);
    fireEvent.change(updatedPageInput, { target: { value: "999" } });
    fireEvent.blur(updatedPageInput);
    await expectCurrentPage(116, 116);
  });

  it("zooms in fixed steps, clamps both bounds, and resets to 100 percent", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const zoomIn = screen.getByRole("button", { name: "Zoom in" });
    const zoomOut = screen.getByRole("button", { name: "Zoom out" });
    const resetZoom = screen.getByRole("button", { name: "Reset zoom" });

    expect(screen.getByText("100%")).toBeInTheDocument();

    fireEvent.click(zoomIn);
    expect(screen.getByText("110%")).toBeInTheDocument();

    for (let index = 0; index < 30; index += 1) {
      fireEvent.click(zoomIn);
    }
    expect(screen.getByText("260%")).toBeInTheDocument();
    expect(zoomIn).toBeDisabled();

    fireEvent.click(resetZoom);
    expect(screen.getByText("100%")).toBeInTheDocument();

    fireEvent.click(zoomOut);
    expect(screen.getByText("90%")).toBeInTheDocument();

    for (let index = 0; index < 30; index += 1) {
      fireEvent.click(zoomOut);
    }
    expect(screen.getByText("60%")).toBeInTheDocument();
    expect(zoomOut).toBeDisabled();
    expect(renderedPages().length).toBeLessThanOrEqual(9);
  });

  it("uses one Ctrl-wheel zoom step inside the scroll container", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const scrollContainer = screen.getByTestId("pdf-scroll-container");
    fireEvent.wheel(scrollContainer, { ctrlKey: true, deltaY: -100 });
    expect(screen.getByText("110%")).toBeInTheDocument();

    fireEvent.wheel(scrollContainer, { ctrlKey: true, deltaY: 100 });
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("keeps ordinary vertical wheel native and enables boundary chaining", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const scrollContainer = screen.getByTestId("pdf-scroll-container");
    const wheelEvent = new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      deltaY: 100,
    });
    scrollContainer.dispatchEvent(wheelEvent);

    expect(wheelEvent.defaultPrevented).toBe(false);
    expect(scrollContainer.style.overscrollBehaviorY).toBe("auto");
    expect(scrollContainer.style.overscrollBehaviorX).toBe("contain");
  });

  it("drags the document vertically with a primary mouse pointer and restores the cursor on release", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const scrollContainer = screen.getByTestId("pdf-scroll-container");
    const blankTextLayer = screen.getByTestId("mock-text-layer-1");
    const pointerCapture = mockPointerCapture(scrollContainer);
    scrollContainer.scrollTop = 300;

    expect(scrollContainer).toHaveClass("cursor-grab");

    dispatchPointerEvent(blankTextLayer, "pointerdown", {
      button: 0,
      buttons: 1,
      clientY: 480,
      isPrimary: true,
      pointerId: 7,
      pointerType: "mouse",
    });

    expect(pointerCapture.setPointerCapture).toHaveBeenCalledWith(7);
    expect(scrollContainer).toHaveClass("cursor-grab");

    dispatchPointerEvent(scrollContainer, "pointermove", {
      buttons: 1,
      clientY: 478,
      isPrimary: true,
      pointerId: 7,
      pointerType: "mouse",
    });
    expect(scrollContainer.scrollTop).toBe(300);
    expect(scrollContainer).toHaveClass("cursor-grab");

    dispatchPointerEvent(scrollContainer, "pointermove", {
      buttons: 1,
      clientY: 360,
      isPrimary: true,
      pointerId: 7,
      pointerType: "mouse",
    });
    expect(scrollContainer.scrollTop).toBe(420);
    expect(scrollContainer).toHaveClass("cursor-grabbing");

    dispatchPointerEvent(scrollContainer, "pointermove", {
      buttons: 1,
      clientY: 410,
      isPrimary: true,
      pointerId: 7,
      pointerType: "mouse",
    });
    expect(scrollContainer.scrollTop).toBe(370);

    dispatchPointerEvent(scrollContainer, "pointerup", {
      button: 0,
      buttons: 0,
      clientY: 410,
      isPrimary: true,
      pointerId: 7,
      pointerType: "mouse",
    });

    expect(pointerCapture.releasePointerCapture).toHaveBeenCalledWith(7);
    expect(scrollContainer).toHaveClass("cursor-grab");

    dispatchPointerEvent(scrollContainer, "pointermove", {
      buttons: 1,
      clientY: 300,
      isPrimary: true,
      pointerId: 7,
      pointerType: "mouse",
    });
    expect(scrollContainer.scrollTop).toBe(370);
  });

  it("ends drag state when pointer capture is cancelled or lost and cleans it up on unmount", async () => {
    const view = render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const scrollContainer = screen.getByTestId("pdf-scroll-container");
    const pointerCapture = mockPointerCapture(scrollContainer);

    dispatchPointerEvent(scrollContainer, "pointerdown", {
      button: 0,
      buttons: 1,
      clientY: 500,
      isPrimary: true,
      pointerId: 11,
      pointerType: "mouse",
    });
    expect(scrollContainer).toHaveClass("cursor-grab");

    dispatchPointerEvent(scrollContainer, "pointercancel", {
      pointerId: 11,
      pointerType: "mouse",
    });
    expect(scrollContainer).toHaveClass("cursor-grab");

    dispatchPointerEvent(scrollContainer, "pointerdown", {
      button: 0,
      buttons: 1,
      clientY: 500,
      isPrimary: true,
      pointerId: 12,
      pointerType: "mouse",
    });
    dispatchPointerEvent(scrollContainer, "lostpointercapture", {
      pointerId: 12,
      pointerType: "mouse",
    });
    expect(scrollContainer).toHaveClass("cursor-grab");

    dispatchPointerEvent(scrollContainer, "pointerdown", {
      button: 0,
      buttons: 1,
      clientY: 500,
      isPrimary: true,
      pointerId: 13,
      pointerType: "mouse",
    });
    dispatchPointerEvent(scrollContainer, "pointermove", {
      buttons: 1,
      clientY: 450,
      isPrimary: true,
      pointerId: 13,
      pointerType: "mouse",
    });
    expect(scrollContainer).toHaveClass("cursor-grabbing");

    view.unmount();
    expect(pointerCapture.releasePointerCapture).toHaveBeenCalledWith(13);
  });

  it("preserves text selection, links, and form controls instead of starting a drag", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const scrollContainer = screen.getByTestId("pdf-scroll-container");
    mockPointerCapture(scrollContainer);
    scrollContainer.scrollTop = 240;

    const interactiveTargets = [
      screen.getByTestId("mock-text-1"),
      screen.getByTestId("mock-link-1"),
      screen.getByTestId("mock-input-1"),
    ];

    for (const target of interactiveTargets) {
      const pointerDown = dispatchPointerEvent(target, "pointerdown", {
        button: 0,
        buttons: 1,
        clientY: 500,
        isPrimary: true,
        pointerId: 21,
        pointerType: "mouse",
      });
      expect(pointerDown.defaultPrevented).toBe(false);
      expect(scrollContainer).toHaveClass("cursor-grab");

      dispatchPointerEvent(scrollContainer, "pointermove", {
        buttons: 1,
        clientY: 300,
        isPrimary: true,
        pointerId: 21,
        pointerType: "mouse",
      });
      expect(scrollContainer.scrollTop).toBe(240);
    }

    const input = screen.getByTestId("mock-input-1");
    input.focus();
    expect(input).toHaveFocus();
  });

  it("leaves touch and non-left mouse gestures to their native behavior", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const scrollContainer = screen.getByTestId("pdf-scroll-container");
    const pointerCapture = mockPointerCapture(scrollContainer);
    scrollContainer.scrollTop = 180;

    const ignoredPointers: MockPointerEventInit[] = [
      {
        button: 0,
        buttons: 1,
        clientY: 500,
        isPrimary: true,
        pointerId: 31,
        pointerType: "touch",
      },
      {
        button: 2,
        buttons: 2,
        clientY: 500,
        isPrimary: true,
        pointerId: 32,
        pointerType: "mouse",
      },
    ];

    for (const pointer of ignoredPointers) {
      const pointerDown = dispatchPointerEvent(
        scrollContainer,
        "pointerdown",
        pointer,
      );
      dispatchPointerEvent(scrollContainer, "pointermove", {
        ...pointer,
        clientY: 300,
      });

      expect(pointerDown.defaultPrevented).toBe(false);
      expect(scrollContainer.scrollTop).toBe(180);
      expect(scrollContainer).toHaveClass("cursor-grab");
    }

    expect(pointerCapture.setPointerCapture).not.toHaveBeenCalled();
  });

  it("drops the previous virtual window and page state when the file changes", async () => {
    const view = render(
      <PDFChatViewer
        fileUrl="first-report.pdf"
        targetPage={77}
        targetPageNonce={1}
      />,
    );
    await expectDocumentReady(116);
    await expectCurrentPage(77, 116);
    await waitFor(() => {
      expect(renderedPageNumbers()).toContain(77);
    });

    view.rerender(<PDFChatViewer fileUrl="ten-pages.pdf" />);

    await expectDocumentReady(10);
    await expectCurrentPage(1, 10);
    expect(renderedPageNumbers()).not.toContain(77);
    expect(renderedPageNumbers()).toContain(1);
    expect(renderedPages().length).toBeLessThanOrEqual(9);
  });

  it("shows loading and error fallbacks and can recover with another file", async () => {
    const view = render(<PDFChatViewer fileUrl="loading.pdf" />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading pdf/i);
    expect(renderedPages()).toHaveLength(0);

    view.rerender(<PDFChatViewer fileUrl="error.pdf" />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/failed to load pdf/i);
    });
    expect(renderedPages()).toHaveLength(0);

    view.rerender(<PDFChatViewer fileUrl="ten-pages.pdf" />);
    await expectDocumentReady(10);
    await expectCurrentPage(1, 10);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
