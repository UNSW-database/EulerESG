"use client";

import {
  useCallback,
  useEffect,
  useRef,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";

const DRAG_THRESHOLD_PX = 6;
const VIEWPORT_MARGIN_PX = 8;

export interface FloatingPosition {
  x: number;
  y: number;
}

interface DraggableFloatingOptions {
  position?: FloatingPosition | null;
  onPositionChange?: (position: FloatingPosition) => void;
}

interface DragSession<T extends HTMLElement> {
  activated: boolean;
  element: T;
  height: number;
  lastPosition: FloatingPosition | null;
  pointerId: number;
  startClientX: number;
  startClientY: number;
  startLeft: number;
  startTop: number;
  width: number;
}

function getViewportBounds() {
  const viewport = window.visualViewport;
  const left = viewport?.offsetLeft ?? 0;
  const top = viewport?.offsetTop ?? 0;
  const width = viewport?.width ?? window.innerWidth;
  const height = viewport?.height ?? window.innerHeight;

  return {
    bottom: top + height,
    left,
    right: left + width,
    top,
  };
}

function clampToViewport(
  point: FloatingPosition,
  width: number,
  height: number,
): FloatingPosition {
  const viewport = getViewportBounds();
  const minX = viewport.left + VIEWPORT_MARGIN_PX;
  const minY = viewport.top + VIEWPORT_MARGIN_PX;
  const maxX = Math.max(
    minX,
    viewport.right - Math.max(0, width) - VIEWPORT_MARGIN_PX,
  );
  const maxY = Math.max(
    minY,
    viewport.bottom - Math.max(0, height) - VIEWPORT_MARGIN_PX,
  );

  return {
    x: Math.min(maxX, Math.max(minX, point.x)),
    y: Math.min(maxY, Math.max(minY, point.y)),
  };
}

function samePoint(left: FloatingPosition, right: FloatingPosition): boolean {
  return left.x === right.x && left.y === right.y;
}

/**
 * Make a fixed floating launcher pointer-draggable without turning a short
 * tap/click into a drag. Position updates are applied directly while moving so
 * a large page does not re-render for every pointer event.
 */
export function useDraggableFloating<T extends HTMLElement>({
  position = null,
  onPositionChange,
}: DraggableFloatingOptions = {}) {
  const draggableRef = useRef<T>(null);
  const dragSessionRef = useRef<DragSession<T> | null>(null);
  const positionRef = useRef<FloatingPosition | null>(position);
  const suppressClickRef = useRef(false);
  const suppressClickTimerRef = useRef<number | null>(null);

  const clearClickSuppressionTimer = useCallback(() => {
    if (suppressClickTimerRef.current !== null) {
      window.clearTimeout(suppressClickTimerRef.current);
      suppressClickTimerRef.current = null;
    }
  }, []);

  const applyPosition = useCallback((element: T, nextPosition: FloatingPosition) => {
    element.style.left = `${nextPosition.x}px`;
    element.style.top = `${nextPosition.y}px`;
    element.style.right = "auto";
    element.style.bottom = "auto";
  }, []);

  const stopPointerDrag = useCallback(
    (pointerId: number, suppressClick: boolean, releaseCapture = true) => {
      const session = dragSessionRef.current;
      if (!session || session.pointerId !== pointerId) return;

      dragSessionRef.current = null;
      session.element.dataset.dragging = "false";

      if (
        releaseCapture &&
        session.element.hasPointerCapture?.(session.pointerId)
      ) {
        session.element.releasePointerCapture?.(session.pointerId);
      }

      if (session.lastPosition) {
        positionRef.current = session.lastPosition;
        onPositionChange?.(session.lastPosition);
      }

      clearClickSuppressionTimer();
      suppressClickRef.current = suppressClick;
      if (suppressClick) {
        // Native click follows pointerup in the same task. Clear the guard on
        // the next task so a later keyboard click is never swallowed.
        suppressClickTimerRef.current = window.setTimeout(() => {
          suppressClickRef.current = false;
          suppressClickTimerRef.current = null;
        }, 0);
      }
    },
    [clearClickSuppressionTimer, onPositionChange],
  );

  useEffect(() => {
    const element = draggableRef.current;
    if (!element) return;

    if (!position) {
      positionRef.current = null;
      element.style.removeProperty("left");
      element.style.removeProperty("top");
      element.style.removeProperty("right");
      element.style.removeProperty("bottom");
      return;
    }

    const rect = element.getBoundingClientRect();
    const nextPosition = clampToViewport(position, rect.width, rect.height);
    positionRef.current = nextPosition;
    applyPosition(element, nextPosition);
    if (!samePoint(position, nextPosition)) {
      onPositionChange?.(nextPosition);
    }
  }, [applyPosition, onPositionChange, position]);

  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<T>) => {
      if (event.button !== 0 || event.isPrimary === false) return;

      clearClickSuppressionTimer();
      suppressClickRef.current = false;

      const rect = event.currentTarget.getBoundingClientRect();
      dragSessionRef.current = {
        activated: false,
        element: event.currentTarget,
        height: rect.height,
        lastPosition: null,
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startLeft: rect.left,
        startTop: rect.top,
        width: rect.width,
      };
      event.currentTarget.dataset.dragging = "false";

      try {
        event.currentTarget.setPointerCapture?.(event.pointerId);
      } catch {
        // Pointer capture is an enhancement; the element-level handlers still
        // preserve normal clicking when a browser does not support it.
      }
    },
    [clearClickSuppressionTimer],
  );

  const handlePointerMove = useCallback(
    (event: ReactPointerEvent<T>) => {
      const session = dragSessionRef.current;
      if (!session || session.pointerId !== event.pointerId) return;

      const deltaX = event.clientX - session.startClientX;
      const deltaY = event.clientY - session.startClientY;
      if (
        !session.activated &&
        Math.hypot(deltaX, deltaY) < DRAG_THRESHOLD_PX
      ) {
        return;
      }

      if (!session.activated) {
        session.activated = true;
        session.element.dataset.dragging = "true";
      }

      event.preventDefault();
      const nextPosition = clampToViewport(
        {
          x: session.startLeft + deltaX,
          y: session.startTop + deltaY,
        },
        session.width,
        session.height,
      );
      session.lastPosition = nextPosition;
      applyPosition(session.element, nextPosition);
    },
    [applyPosition],
  );

  const handlePointerUp = useCallback(
    (event: ReactPointerEvent<T>) => {
      const session = dragSessionRef.current;
      if (!session || session.pointerId !== event.pointerId) return;
      stopPointerDrag(event.pointerId, session.activated);
    },
    [stopPointerDrag],
  );

  const handlePointerCancel = useCallback(
    (event: ReactPointerEvent<T>) => {
      stopPointerDrag(event.pointerId, false);
    },
    [stopPointerDrag],
  );

  const handleLostPointerCapture = useCallback(
    (event: ReactPointerEvent<T>) => {
      const session = dragSessionRef.current;
      stopPointerDrag(
        event.pointerId,
        Boolean(session?.activated),
        false,
      );
    },
    [stopPointerDrag],
  );

  const handleClickCapture = useCallback(
    (event: ReactMouseEvent<T>) => {
      if (!suppressClickRef.current) return;

      clearClickSuppressionTimer();
      suppressClickRef.current = false;
      if (event.detail === 0) return;

      event.preventDefault();
      event.stopPropagation();
    },
    [clearClickSuppressionTimer],
  );

  useEffect(() => {
    const keepInsideViewport = () => {
      const element = draggableRef.current;
      const currentPosition = positionRef.current;
      if (!element || !currentPosition) return;

      const rect = element.getBoundingClientRect();
      const nextPosition = clampToViewport(
        currentPosition,
        rect.width,
        rect.height,
      );
      if (samePoint(currentPosition, nextPosition)) return;

      positionRef.current = nextPosition;
      applyPosition(element, nextPosition);
      onPositionChange?.(nextPosition);
    };

    const viewport = window.visualViewport;
    window.addEventListener("resize", keepInsideViewport);
    window.addEventListener("orientationchange", keepInsideViewport);
    viewport?.addEventListener("resize", keepInsideViewport);
    viewport?.addEventListener("scroll", keepInsideViewport);

    return () => {
      window.removeEventListener("resize", keepInsideViewport);
      window.removeEventListener("orientationchange", keepInsideViewport);
      viewport?.removeEventListener("resize", keepInsideViewport);
      viewport?.removeEventListener("scroll", keepInsideViewport);
    };
  }, [applyPosition, onPositionChange]);

  useEffect(() => {
    const stopOnWindowBlur = () => {
      const session = dragSessionRef.current;
      if (session) stopPointerDrag(session.pointerId, false);
    };
    window.addEventListener("blur", stopOnWindowBlur);

    return () => {
      window.removeEventListener("blur", stopOnWindowBlur);
      clearClickSuppressionTimer();
      const session = dragSessionRef.current;
      dragSessionRef.current = null;
      if (session?.element.hasPointerCapture?.(session.pointerId)) {
        session.element.releasePointerCapture?.(session.pointerId);
      }
    };
  }, [clearClickSuppressionTimer, stopPointerDrag]);

  return {
    draggableProps: {
      "data-draggable-assistant": "true",
      onClickCapture: handleClickCapture,
      onLostPointerCapture: handleLostPointerCapture,
      onPointerCancel: handlePointerCancel,
      onPointerDown: handlePointerDown,
      onPointerMove: handlePointerMove,
      onPointerUp: handlePointerUp,
    },
    draggableRef,
  };
}
