import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  useDraggableFloating,
  type FloatingPosition,
} from "@/hooks/useDraggableFloating";

type MockPointerEventInit = MouseEventInit & {
  isPrimary?: boolean;
  pointerId?: number;
  pointerType?: string;
};

const dispatchPointerEvent = (
  target: Node,
  type: string,
  init: MockPointerEventInit,
) => {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    ...init,
  });
  Object.defineProperties(event, {
    isPrimary: { configurable: true, value: init.isPrimary ?? true },
    pointerId: { configurable: true, value: init.pointerId ?? 1 },
    pointerType: { configurable: true, value: init.pointerType ?? "mouse" },
  });
  fireEvent(target, event);
};

function DraggableHarness({
  onPositionChange,
  position,
}: {
  onPositionChange: (position: FloatingPosition) => void;
  position?: FloatingPosition | null;
}) {
  const { draggableProps, draggableRef } =
    useDraggableFloating<HTMLButtonElement>({
      onPositionChange,
      position,
    });

  return (
    <button ref={draggableRef} type="button" {...draggableProps}>
      Assistant launcher
    </button>
  );
}

describe("useDraggableFloating controlled position", () => {
  it("publishes the final drag position and restores it in a remounted launcher", () => {
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(800);
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(600);
    let savedPosition: FloatingPosition | null = null;
    const onPositionChange = vi.fn((position: FloatingPosition) => {
      savedPosition = position;
    });
    const firstMount = render(
      <DraggableHarness onPositionChange={onPositionChange} />,
    );
    const launcher = screen.getByRole("button", {
      name: "Assistant launcher",
    });
    vi.spyOn(launcher, "getBoundingClientRect").mockReturnValue({
      bottom: 548,
      height: 48,
      left: 24,
      right: 184,
      top: 500,
      width: 160,
      x: 24,
      y: 500,
      toJSON: () => ({}),
    });

    dispatchPointerEvent(launcher, "pointerdown", {
      button: 0,
      clientX: 50,
      clientY: 520,
      pointerId: 7,
    });
    dispatchPointerEvent(launcher, "pointermove", {
      button: 0,
      clientX: 760,
      clientY: -100,
      pointerId: 7,
    });
    dispatchPointerEvent(launcher, "pointerup", {
      button: 0,
      clientX: 760,
      clientY: -100,
      pointerId: 7,
    });

    expect(onPositionChange).toHaveBeenLastCalledWith({ x: 632, y: 8 });
    expect(savedPosition).toEqual({ x: 632, y: 8 });
    firstMount.unmount();

    const secondMount = render(
      <DraggableHarness onPositionChange={onPositionChange} position={null} />,
    );
    const remountedLauncher = screen.getByRole("button", {
      name: "Assistant launcher",
    });
    vi.spyOn(remountedLauncher, "getBoundingClientRect").mockReturnValue({
      bottom: 56,
      height: 48,
      left: 8,
      right: 168,
      top: 8,
      width: 160,
      x: 8,
      y: 8,
      toJSON: () => ({}),
    });
    secondMount.rerender(
      <DraggableHarness
        onPositionChange={onPositionChange}
        position={savedPosition}
      />,
    );

    expect(remountedLauncher).toHaveStyle({
      bottom: "auto",
      left: "632px",
      right: "auto",
      top: "8px",
    });
  });
});
