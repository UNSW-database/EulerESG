import React from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MetricChartsGrid,
  type MetricChartSpec,
} from "../MetricChartsGrid";

const mocks = vi.hoisted(() => ({
  mount: vi.fn(),
  nextMountId: 0,
  triggerResize: vi.fn(),
  unmount: vi.fn(),
}));

vi.mock("next/dynamic", async () => {
  const ReactModule = await import("react");

  function MockColumn(props: Record<string, unknown>) {
    const [mountId] = ReactModule.useState(() => ++mocks.nextMountId);

    ReactModule.useEffect(() => {
      mocks.mount(mountId);
      const onReady = props.onReady as
        | ((plot: { triggerResize: () => void }) => void)
        | undefined;
      onReady?.({ triggerResize: mocks.triggerResize });

      return () => {
        mocks.unmount(mountId);
      };
    }, []);

    return ReactModule.createElement("div", {
      "data-auto-fit": String(props.autoFit),
      "data-mount-id": String(mountId),
      "data-testid": "column-plot",
    });
  }

  return {
    default: () => MockColumn,
  };
});

vi.mock("antd", async () => {
  const ReactModule = await import("react");
  return {
    Empty: ({ description }: { description?: React.ReactNode }) =>
      ReactModule.createElement("div", null, description),
  };
});

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

function domRect(width: number, height = 320): DOMRectReadOnly {
  return {
    bottom: height,
    height,
    left: 0,
    right: width,
    top: 0,
    width,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  };
}

class ControllableResizeObserver implements ResizeObserver {
  static instances: ControllableResizeObserver[] = [];

  readonly targets = new Set<Element>();

  constructor(private readonly callback: ResizeObserverCallback) {
    ControllableResizeObserver.instances.push(this);
  }

  observe(target: Element): void {
    this.targets.add(target);
  }

  unobserve(target: Element): void {
    this.targets.delete(target);
  }

  disconnect(): void {
    this.targets.clear();
  }

  static reset(): void {
    ControllableResizeObserver.instances = [];
  }

  static trigger(target: Element, width: number): boolean {
    const observer = ControllableResizeObserver.instances.find((candidate) =>
      candidate.targets.has(target),
    );
    if (!observer) return false;

    const contentRect = domRect(width);
    const entry = {
      borderBoxSize: [],
      contentBoxSize: [],
      contentRect,
      devicePixelContentBoxSize: [],
      target,
    } satisfies ResizeObserverEntry;

    observer.callback([entry], observer);
    return true;
  }
}

class ControllableIntersectionObserver implements IntersectionObserver {
  static instances: ControllableIntersectionObserver[] = [];

  readonly root = null;
  readonly rootMargin: string;
  readonly thresholds: readonly number[];
  readonly targets = new Set<Element>();

  constructor(
    private readonly callback: IntersectionObserverCallback,
    options?: IntersectionObserverInit,
  ) {
    this.rootMargin = options?.rootMargin ?? "0px";
    const threshold = options?.threshold ?? 0;
    this.thresholds = Array.isArray(threshold) ? threshold : [threshold];
    ControllableIntersectionObserver.instances.push(this);
  }

  observe(target: Element): void {
    this.targets.add(target);
  }

  unobserve(target: Element): void {
    this.targets.delete(target);
  }

  disconnect(): void {
    this.targets.clear();
  }

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }

  static reset(): void {
    ControllableIntersectionObserver.instances = [];
  }

  static trigger(target: Element, isIntersecting: boolean): boolean {
    const observer = ControllableIntersectionObserver.instances.find(
      (candidate) => candidate.targets.has(target),
    );
    if (!observer) return false;

    const targetRect = domRect(640, 418);
    const intersectionRect = isIntersecting ? targetRect : domRect(0, 0);
    const entry = {
      boundingClientRect: targetRect,
      intersectionRatio: isIntersecting ? 1 : 0,
      intersectionRect,
      isIntersecting,
      rootBounds: null,
      target,
      time: 0,
    } satisfies IntersectionObserverEntry;
    observer.callback([entry], observer);
    return true;
  }
}

const originalResizeObserver = globalThis.ResizeObserver;
const originalIntersectionObserver = globalThis.IntersectionObserver;

const companyColors = {
  "report-a": "#1677ff",
  "report-b": "#52c41a",
};

function charts(count = 1): MetricChartSpec[] {
  return Array.from({ length: count }, (_, index) => ({
    key: `metric-${index + 1}`,
    points: [
      {
        colorKey: "report-a",
        company: "Report A",
        value: 40 + index,
        year: "2025",
      },
      {
        colorKey: "report-b",
        company: "Report B",
        value: 50 + index,
        year: "2025",
      },
    ],
    topic: `Metric ${index + 1}`,
    unit: "%",
    yearInfo: "FY2025",
  }));
}

function setDevicePixelRatio(value: number): void {
  Object.defineProperty(window, "devicePixelRatio", {
    configurable: true,
    value,
  });
}

describe("MetricChartsGrid responsive rendering", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.nextMountId = 0;
    setDevicePixelRatio(1);
    ControllableResizeObserver.reset();
    ControllableIntersectionObserver.reset();
    Object.defineProperty(globalThis, "ResizeObserver", {
      configurable: true,
      value: ControllableResizeObserver,
      writable: true,
    });
    Object.defineProperty(globalThis, "IntersectionObserver", {
      configurable: true,
      value: ControllableIntersectionObserver,
      writable: true,
    });
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    Object.defineProperty(globalThis, "ResizeObserver", {
      configurable: true,
      value: originalResizeObserver,
      writable: true,
    });
    Object.defineProperty(globalThis, "IntersectionObserver", {
      configurable: true,
      value: originalIntersectionObserver,
      writable: true,
    });
  });

  it("remounts the canvas after DPR changes settle, but not for a same-DPR resize", () => {
    render(<MetricChartsGrid charts={charts()} companyColors={companyColors} />);

    const firstMountId = screen.getByTestId("column-plot").dataset.mountId;
    expect(mocks.mount).toHaveBeenCalledTimes(1);

    act(() => {
      window.dispatchEvent(new Event("resize"));
      vi.advanceTimersByTime(400);
    });

    expect(screen.getByTestId("column-plot")).toHaveAttribute(
      "data-mount-id",
      firstMountId,
    );
    expect(mocks.mount).toHaveBeenCalledTimes(1);
    expect(mocks.unmount).not.toHaveBeenCalled();

    setDevicePixelRatio(2);
    act(() => {
      window.dispatchEvent(new Event("resize"));
      vi.advanceTimersByTime(399);
    });

    expect(screen.getByTestId("column-plot")).toHaveAttribute(
      "data-mount-id",
      firstMountId,
    );

    act(() => {
      vi.advanceTimersByTime(1);
    });

    expect(screen.getByTestId("column-plot").dataset.mountId).not.toBe(
      firstMountId,
    );
    expect(mocks.mount).toHaveBeenCalledTimes(2);
    expect(mocks.unmount).toHaveBeenCalledTimes(1);
  });

  it("asks the ready plot to resize 240 ms after its host width changes", () => {
    render(<MetricChartsGrid charts={charts()} companyColors={companyColors} />);

    const plot = screen.getByTestId("column-plot");
    const host = plot.parentElement;
    expect(host).not.toBeNull();
    expect(ControllableResizeObserver.trigger(host!, 640)).toBe(true);
    expect(mocks.triggerResize).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(239);
    });
    expect(mocks.triggerResize).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(mocks.triggerResize).toHaveBeenCalledTimes(1);
  });

  it("uses a container auto-fit grid without viewport column spans", () => {
    const view = render(
      <MetricChartsGrid charts={charts(4)} companyColors={companyColors} />,
    );

    const grid = screen.getByTestId("metric-charts-grid");
    expect(grid).toHaveClass("grid");
    expect(grid.style.gridTemplateColumns).toBe(
      "repeat(auto-fit, minmax(min(100%, 20rem), 1fr))",
    );
    expect(screen.getAllByTestId("column-plot")).toHaveLength(4);
    screen.getAllByTestId("column-plot").forEach((plot) => {
      expect(plot).toHaveAttribute("data-auto-fit", "true");
    });

    const responsiveViewportClasses = Array.from(
      view.container.querySelectorAll<HTMLElement>("[class]"),
    ).flatMap((element) => element.className.split(/\s+/));
    expect(responsiveViewportClasses).not.toContain("md:grid-cols-12");
    expect(
      responsiveViewportClasses.some((className) =>
        className.startsWith("md:col-span-"),
      ),
    ).toBe(false);
  });

  it("mounts a small eager batch and reveals deferred plots near the viewport", () => {
    render(<MetricChartsGrid charts={charts(8)} companyColors={companyColors} />);

    expect(screen.getAllByTestId("column-plot")).toHaveLength(4);
    expect(screen.getAllByTestId("metric-chart-placeholder")).toHaveLength(4);

    const regions = screen.getAllByTestId("metric-chart-region");
    expect(regions).toHaveLength(8);
    regions.slice(0, 4).forEach((region) => {
      expect(region).toHaveAttribute("aria-busy", "false");
      expect(region).toHaveAttribute("data-chart-state", "ready");
      expect(region).toHaveStyle({ minHeight: "418px" });
    });
    regions.slice(4).forEach((region, index) => {
      expect(region).toHaveAttribute("aria-busy", "true");
      expect(region).toHaveAttribute("data-chart-state", "deferred");
      expect(region).toHaveAttribute(
        "aria-label",
        `crossAnalysis.comparisonChartTitle: Metric ${index + 5}`,
      );
    });
    screen.getAllByTestId("metric-chart-placeholder").forEach((placeholder) => {
      expect(placeholder).toHaveStyle({ height: "418px" });
    });

    const firstDeferredRegion = regions[4];
    const observer = ControllableIntersectionObserver.instances.find(
      (candidate) => candidate.targets.has(firstDeferredRegion),
    );
    expect(observer).toBeDefined();
    expect(observer?.rootMargin).toBe("600px 0px");
    expect(observer?.thresholds).toEqual([0.01]);

    act(() => {
      expect(
        ControllableIntersectionObserver.trigger(firstDeferredRegion, true),
      ).toBe(true);
    });

    expect(screen.getAllByTestId("column-plot")).toHaveLength(5);
    expect(screen.getAllByTestId("metric-chart-placeholder")).toHaveLength(3);
    expect(firstDeferredRegion).toHaveAttribute("aria-busy", "false");
    expect(firstDeferredRegion).toHaveAttribute("data-chart-state", "ready");
    expect(observer?.targets.has(firstDeferredRegion)).toBe(false);
  });

  it("falls back to mounting every plot when IntersectionObserver is unavailable", () => {
    Object.defineProperty(globalThis, "IntersectionObserver", {
      configurable: true,
      value: undefined,
      writable: true,
    });

    render(<MetricChartsGrid charts={charts(6)} companyColors={companyColors} />);

    expect(screen.getAllByTestId("column-plot")).toHaveLength(6);
    expect(screen.queryByTestId("metric-chart-placeholder")).not.toBeInTheDocument();
  });
});
