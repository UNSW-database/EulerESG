import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiService } from "@/lib/api";

function successfulResponse(payload: unknown): Response {
  return {
    json: vi.fn().mockResolvedValue(payload),
    ok: true,
    status: 200,
    text: vi.fn().mockResolvedValue(""),
  } as unknown as Response;
}

describe("cross-analysis request cache", () => {
  beforeEach(() => {
    apiService.invalidateCrossAnalysisCache();
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("starts both resources in parallel and reuses them for page loading", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/reports?")) {
        return Promise.resolve(successfulResponse({ reports: [] }));
      }
      return Promise.resolve(successfulResponse({ records: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const ids = [" report-a ", "report-b", "report-a"];
    apiService.prefetchCrossAnalysis(ids);

    const [reports, records] = await Promise.all([
      apiService.getCrossAnalysisReports(["report-a", "report-b"]),
      apiService.getCrossAnalysisDisclosedCache(["report-a", "report-b"]),
    ]);

    expect(reports).toEqual({ reports: [] });
    expect(records).toEqual({ records: [] });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual(
      expect.arrayContaining([
        "/api/cross-analysis/reports?ids=report-a%2Creport-b",
        "/api/cross-analysis/disclosed-cache?ids=report-a%2Creport-b",
      ]),
    );
  });

  it("never expires an in-flight request and evicts a failed request", async () => {
    vi.useFakeTimers();
    let rejectFirst!: (reason: Error) => void;
    const firstFetch = new Promise<Response>((_resolve, reject) => {
      rejectFirst = reject;
    });
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(firstFetch)
      .mockResolvedValueOnce(successfulResponse({ reports: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const first = apiService.getCrossAnalysisReports(["a", "b"]);
    vi.advanceTimersByTime(60_000);
    const whilePending = apiService.getCrossAnalysisReports(["a", "b"]);

    expect(whilePending).toBe(first);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    rejectFirst(new Error("network failed"));
    await expect(first).rejects.toThrow("network failed");

    await expect(apiService.getCrossAnalysisReports(["a", "b"])).resolves.toEqual({ reports: [] });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("invalidates cached comparison data after a successful report deletion", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(successfulResponse({ reports: [{ file_id: "a" }] }))
      .mockResolvedValueOnce(successfulResponse({ status: "success" }))
      .mockResolvedValueOnce(successfulResponse({ reports: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await apiService.getCrossAnalysisReports(["a", "b"]);
    await apiService.deleteFile("a");
    await expect(apiService.getCrossAnalysisReports(["a", "b"])).resolves.toEqual({ reports: [] });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/files/a");
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "DELETE" });
  });
});
