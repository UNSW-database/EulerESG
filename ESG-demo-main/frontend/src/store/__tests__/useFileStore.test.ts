import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  deleteFile: vi.fn(),
  getFiles: vi.fn(),
  invalidateAssessmentByFileCache: vi.fn(),
  invalidateVisualAssetCache: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  apiService: apiMocks,
}));

import { useFileStore } from "@/store/useFileStore";

const backendFile = (overrides: Record<string, unknown> = {}) => ({
  file_id: "report-1",
  original_name: "report.pdf",
  file_size: 1024 * 1024,
  upload_time: "2026-08-21T01:02:03Z",
  file_type: "report",
  status: "processed",
  framework: "SASB",
  industry: "Technology & Communications",
  semi_industry: "Software & IT Services",
  total_pages: 42,
  ...overrides,
});

const successfulResponse = (overrides: Record<string, unknown> = {}) => ({
  status: "success",
  files: [backendFile(overrides)],
});

describe("useFileStore", () => {
  beforeEach(() => {
    window.localStorage.clear();
    apiMocks.deleteFile.mockReset();
    apiMocks.getFiles.mockReset();
    apiMocks.invalidateAssessmentByFileCache.mockReset();
    apiMocks.invalidateVisualAssetCache.mockReset();
    useFileStore.setState({
      files: [],
      selectedFileId: null,
      selectedFileScopeKey: null,
      crossAnalysisSelection: null,
      loading: false,
      lastRefresh: 0,
    });
  });

  it("sets the compliance file and scope atomically", () => {
    const observedSelections: Array<[string | null, string | null]> = [];
    const unsubscribe = useFileStore.subscribe((state) => {
      observedSelections.push([
        state.selectedFileId,
        state.selectedFileScopeKey,
      ]);
    });

    useFileStore
      .getState()
      .setComplianceSelection(" report-a ", " scope-a ");
    unsubscribe();

    expect(observedSelections).toEqual([["report-a", "scope-a"]]);
    expect(useFileStore.getState()).toMatchObject({
      selectedFileId: "report-a",
      selectedFileScopeKey: "scope-a",
    });
  });

  it("persists compliance and cross-analysis selections", () => {
    const crossAnalysisSelection = {
      href: "/cross-analysis/environment?ids=report-a%2Creport-b",
      reports: [
        { fileId: "report-a", scopeKey: "scope-a" },
        { fileId: "report-b", scopeKey: "scope-b" },
      ],
    };

    useFileStore
      .getState()
      .setComplianceSelection("report-a", "scope-a");
    useFileStore
      .getState()
      .setCrossAnalysisSelection(crossAnalysisSelection);

    const persisted = JSON.parse(
      window.localStorage.getItem("file-storage") || "{}",
    );
    expect(persisted.state).toEqual({
      selectedFileId: "report-a",
      selectedFileScopeKey: "scope-a",
      crossAnalysisSelection,
    });
  });

  it("clears compliance and cross-analysis selections with account state", () => {
    useFileStore.setState({
      selectedFileId: "report-a",
      selectedFileScopeKey: "scope-a",
      crossAnalysisSelection: {
        href: "/cross-analysis/environment?ids=report-a%2Creport-b",
        reports: [
          { fileId: "report-a", scopeKey: "scope-a" },
          { fileId: "report-b", scopeKey: "scope-b" },
        ],
      },
    });

    useFileStore.getState().clearFiles();

    expect(useFileStore.getState()).toMatchObject({
      files: [],
      selectedFileId: null,
      selectedFileScopeKey: null,
      crossAnalysisSelection: null,
    });
    expect(apiMocks.invalidateAssessmentByFileCache).toHaveBeenCalledTimes(1);
    expect(apiMocks.invalidateVisualAssetCache).toHaveBeenCalledTimes(1);
  });

  it("clears selections when their selected report scope is deleted", async () => {
    apiMocks.deleteFile.mockResolvedValue({ status: "success" });
    apiMocks.getFiles.mockResolvedValue({ status: "success", files: [] });
    useFileStore.setState({
      selectedFileId: "report-a",
      selectedFileScopeKey: "scope-a",
      crossAnalysisSelection: {
        href: "/cross-analysis/environment?ids=report-a%2Creport-b",
        reports: [
          { fileId: "report-a", scopeKey: "scope-a" },
          { fileId: "report-b", scopeKey: "scope-b" },
        ],
      },
    });

    await useFileStore.getState().deleteFile("report-a", "scope-a");

    expect(apiMocks.deleteFile).toHaveBeenCalledWith("report-a", "scope-a");
    expect(useFileStore.getState()).toMatchObject({
      selectedFileId: null,
      selectedFileScopeKey: null,
      crossAnalysisSelection: null,
    });
  });

  it.each([
    {
      href: "/dashboard/chat?file_id=report-c",
      reports: [
        { fileId: "report-c", scopeKey: "scope-c" },
        { fileId: "report-d", scopeKey: "scope-d" },
      ],
    },
    {
      href: "/cross-analysis/environment?ids=report-c",
      reports: [{ fileId: "report-c", scopeKey: "scope-c" }],
    },
    {
      href: "/cross-analysis/environment?ids=report-c",
      reports: [
        { fileId: "report-c", scopeKey: "scope-c" },
        { fileId: " report-c ", scopeKey: "scope-c" },
      ],
    },
  ])("does not overwrite a valid cross-analysis selection with an invalid one", (invalidSelection) => {
    const validSelection = {
      href: "/cross-analysis/environment?ids=report-a%2Creport-b",
      reports: [
        { fileId: "report-a", scopeKey: "scope-a" },
        { fileId: "report-b", scopeKey: "scope-b" },
      ],
    };
    useFileStore.getState().setCrossAnalysisSelection(validSelection);
    const previousSelection = useFileStore.getState().crossAnalysisSelection;

    useFileStore.getState().setCrossAnalysisSelection(invalidSelection);

    expect(useFileStore.getState().crossAnalysisSelection).toBe(
      previousSelection,
    );
  });

  it("preserves the files array reference when the mapped backend rows are unchanged", async () => {
    apiMocks.getFiles.mockResolvedValue(successfulResponse());

    await useFileStore.getState().loadFilesFromBackend();
    const firstFiles = useFileStore.getState().files;

    await useFileStore.getState().loadFilesFromBackend();

    expect(apiMocks.getFiles).toHaveBeenCalledTimes(2);
    expect(useFileStore.getState().files).toBe(firstFiles);
    expect(useFileStore.getState().files[0]).toMatchObject({
      file_id: "report-1",
      name: "report.pdf",
      pages: "42",
      status: "ready",
    });
  });

  it("replaces the files array when a backend row changes", async () => {
    apiMocks.getFiles
      .mockResolvedValueOnce(successfulResponse())
      .mockResolvedValueOnce(successfulResponse({ total_pages: 43 }));

    await useFileStore.getState().loadFilesFromBackend();
    const firstFiles = useFileStore.getState().files;
    await useFileStore.getState().loadFilesFromBackend();

    expect(useFileStore.getState().files).not.toBe(firstFiles);
    expect(useFileStore.getState().files[0].pages).toBe("43");
  });

  it("shares one in-flight request across concurrent callers", async () => {
    let resolveResponse!: (value: ReturnType<typeof successfulResponse>) => void;
    const response = new Promise<ReturnType<typeof successfulResponse>>((resolve) => {
      resolveResponse = resolve;
    });
    apiMocks.getFiles.mockReturnValue(response);

    const firstRequest = useFileStore.getState().loadFilesFromBackend();
    const secondRequest = useFileStore
      .getState()
      .loadFilesFromBackend({ showLoading: true });

    expect(firstRequest).toBe(secondRequest);
    expect(apiMocks.getFiles).toHaveBeenCalledTimes(1);
    expect(useFileStore.getState().loading).toBe(true);

    resolveResponse(successfulResponse());
    await Promise.all([firstRequest, secondRequest]);

    expect(apiMocks.getFiles).toHaveBeenCalledTimes(1);
    expect(useFileStore.getState().loading).toBe(false);
    expect(useFileStore.getState().files).toHaveLength(1);
  });

  it("queues one trailing request when concurrent mutation refreshes require fresh data", async () => {
    let resolveFirst!: (value: ReturnType<typeof successfulResponse>) => void;
    let resolveSecond!: (value: ReturnType<typeof successfulResponse>) => void;
    const firstResponse = new Promise<ReturnType<typeof successfulResponse>>((resolve) => {
      resolveFirst = resolve;
    });
    const secondResponse = new Promise<ReturnType<typeof successfulResponse>>((resolve) => {
      resolveSecond = resolve;
    });
    apiMocks.getFiles
      .mockReturnValueOnce(firstResponse)
      .mockReturnValueOnce(secondResponse);

    const pollingRequest = useFileStore.getState().loadFilesFromBackend();
    const firstFreshRequest = useFileStore
      .getState()
      .loadFilesFromBackend({ forceFresh: true });
    const secondFreshRequest = useFileStore
      .getState()
      .loadFilesFromBackend({ forceFresh: true });

    expect(firstFreshRequest).toBe(secondFreshRequest);
    expect(apiMocks.getFiles).toHaveBeenCalledTimes(1);

    resolveFirst(successfulResponse({ total_pages: 42 }));
    await pollingRequest;
    await vi.waitFor(() => expect(apiMocks.getFiles).toHaveBeenCalledTimes(2));

    resolveSecond(successfulResponse({ total_pages: 84 }));
    await firstFreshRequest;

    expect(useFileStore.getState().files[0].pages).toBe("84");
  });

  it("isolates an in-flight list request after account state is cleared", async () => {
    let resolveOldAccount!: (value: ReturnType<typeof successfulResponse>) => void;
    const oldAccountResponse = new Promise<ReturnType<typeof successfulResponse>>((resolve) => {
      resolveOldAccount = resolve;
    });
    apiMocks.getFiles
      .mockReturnValueOnce(oldAccountResponse)
      .mockResolvedValueOnce(successfulResponse({ file_id: "report-new" }));

    const oldAccountRequest = useFileStore
      .getState()
      .loadFilesFromBackend({ showLoading: true });
    useFileStore.getState().clearFiles();
    const newAccountRequest = useFileStore.getState().loadFilesFromBackend();

    expect(apiMocks.getFiles).toHaveBeenCalledTimes(2);
    expect(useFileStore.getState().loading).toBe(false);

    await newAccountRequest;
    resolveOldAccount(successfulResponse({ file_id: "report-old" }));
    await oldAccountRequest;

    expect(useFileStore.getState().files).toHaveLength(1);
    expect(useFileStore.getState().files[0].file_id).toBe("report-new");
  });
});
