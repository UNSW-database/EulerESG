import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

type IntersectionTrigger = Pick<
  IntersectionObserverEntry,
  "isIntersecting" | "intersectionRatio"
>;

export class MockIntersectionObserver implements IntersectionObserver {
  static instances: MockIntersectionObserver[] = [];

  readonly root: Element | Document | null;
  readonly rootMargin: string;
  readonly thresholds: readonly number[];
  readonly targets = new Set<Element>();

  constructor(
    private readonly callback: IntersectionObserverCallback,
    options: IntersectionObserverInit = {},
  ) {
    this.root = options.root ?? null;
    this.rootMargin = options.rootMargin ?? "0px";
    this.thresholds = Array.isArray(options.threshold)
      ? options.threshold
      : [options.threshold ?? 0];
    MockIntersectionObserver.instances.push(this);
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
    MockIntersectionObserver.instances = [];
  }

  static trigger(target: Element, state: IntersectionTrigger): boolean {
    const observer = [...MockIntersectionObserver.instances]
      .reverse()
      .find((candidate) => candidate.targets.has(target));

    if (!observer) return false;

    const rect = target.getBoundingClientRect();
    const entry = {
      boundingClientRect: rect,
      intersectionRatio: state.intersectionRatio,
      intersectionRect: state.isIntersecting ? rect : emptyRect(),
      isIntersecting: state.isIntersecting,
      rootBounds: null,
      target,
      time: performance.now(),
    } satisfies IntersectionObserverEntry;

    observer.callback([entry], observer);
    return true;
  }
}

export class MockResizeObserver implements ResizeObserver {
  static instances: MockResizeObserver[] = [];

  readonly targets = new Set<Element>();

  constructor(private readonly callback: ResizeObserverCallback) {
    MockResizeObserver.instances.push(this);
  }

  observe(target: Element): void {
    this.targets.add(target);
    this.callback(
      [
        {
          borderBoxSize: [],
          contentBoxSize: [],
          contentRect: target.getBoundingClientRect(),
          devicePixelContentBoxSize: [],
          target,
        },
      ],
      this,
    );
  }

  unobserve(target: Element): void {
    this.targets.delete(target);
  }

  disconnect(): void {
    this.targets.clear();
  }

  static reset(): void {
    MockResizeObserver.instances = [];
  }
}

function rect(init: Partial<DOMRect> = {}): DOMRect {
  const x = init.x ?? init.left ?? 0;
  const y = init.y ?? init.top ?? 0;
  const width = init.width ?? 784;
  const height = init.height ?? 1000;

  return {
    bottom: init.bottom ?? y + height,
    height,
    left: init.left ?? x,
    right: init.right ?? x + width,
    top: init.top ?? y,
    width,
    x,
    y,
    toJSON: () => ({}),
  };
}

function emptyRect(): DOMRect {
  return rect({ height: 0, width: 0 });
}

Object.defineProperty(globalThis, "IntersectionObserver", {
  configurable: true,
  value: MockIntersectionObserver,
  writable: true,
});

Object.defineProperty(globalThis, "ResizeObserver", {
  configurable: true,
  value: MockResizeObserver,
  writable: true,
});

Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    addEventListener: vi.fn(),
    addListener: vi.fn(),
    dispatchEvent: vi.fn(),
    matches: false,
    media: query,
    onchange: null,
    removeEventListener: vi.fn(),
    removeListener: vi.fn(),
  })),
  writable: true,
});

beforeEach(() => {
  MockIntersectionObserver.reset();
  MockResizeObserver.reset();

  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(900);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    function getBoundingClientRect(this: HTMLElement): DOMRect {
      const pageSlot = this.closest<HTMLElement>("[data-page-number]");
      const pageNumber = Number(pageSlot?.dataset.pageNumber ?? 0);
      const top = pageNumber > 0 ? (pageNumber - 1) * 1016 : 0;
      return rect({ top, y: top });
    },
  );

  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
    writable: true,
  });

  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(function scrollTo(
      this: HTMLElement,
      optionsOrX?: ScrollToOptions | number,
      y?: number,
    ) {
      const top =
        typeof optionsOrX === "number" ? (y ?? 0) : (optionsOrX?.top ?? 0);
      this.scrollTop = top;
    }),
    writable: true,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
