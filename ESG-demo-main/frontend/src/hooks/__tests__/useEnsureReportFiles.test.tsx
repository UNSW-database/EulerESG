import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEnsureReportFiles } from "../useEnsureReportFiles";

const mocks = vi.hoisted(() => ({
  state: {
    files: [] as Array<{ key: string }>,
    lastRefresh: 0,
    loadFilesFromBackend: vi.fn().mockResolvedValue(undefined),
  },
}));

vi.mock("@/store/useFileStore", () => ({
  useFileStore: (selector: (state: typeof mocks.state) => unknown) =>
    selector(mocks.state),
}));

function Harness() {
  useEnsureReportFiles();
  return null;
}

describe("useEnsureReportFiles", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-23T12:00:00Z"));
    mocks.state.files = [];
    mocks.state.lastRefresh = 0;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows loading for the first empty directory request", () => {
    render(<Harness />);

    expect(mocks.state.loadFilesFromBackend).toHaveBeenCalledWith({
      showLoading: true,
    });
  });

  it("reuses a fresh directory snapshot without another request", () => {
    mocks.state.files = [{ key: "report-a" }];
    mocks.state.lastRefresh = Date.now() - 5_000;

    render(<Harness />);

    expect(mocks.state.loadFilesFromBackend).not.toHaveBeenCalled();
  });

  it("refreshes stale populated data without covering the table", () => {
    mocks.state.files = [{ key: "report-a" }];
    mocks.state.lastRefresh = Date.now() - 20_000;

    render(<Harness />);

    expect(mocks.state.loadFilesFromBackend).toHaveBeenCalledWith({
      showLoading: false,
    });
  });
});
