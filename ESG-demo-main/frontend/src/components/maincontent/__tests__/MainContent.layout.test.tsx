import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MainContent from "../MainContent";

const mocks = vi.hoisted(() => ({
  getFieldsValue: vi.fn(() => ({})),
  resetFields: vi.fn(),
  validateFields: vi.fn(),
}));

vi.mock("antd", async () => {
  const React = await import("react");

  const Content = ({ children, style }: Record<string, any>) =>
    React.createElement(
      "div",
      { "data-testid": "mock-layout-content", style },
      children,
    );
  const Layout = Object.assign(
    ({ children, className, style }: Record<string, any>) =>
      React.createElement(
        "div",
        { className, "data-testid": "upload-area-layout", style },
        children,
      ),
    { Content },
  );
  const Dragger = ({ children, style }: Record<string, any>) =>
    React.createElement(
      "div",
      { "data-testid": "mock-upload-dragger", style },
      children,
    );

  return {
    App: {
      useApp: () => ({
        message: {
          destroy: vi.fn(),
          error: vi.fn(),
          open: vi.fn(),
          success: vi.fn(),
        },
      }),
    },
    Form: {
      useForm: () => [
        {
          getFieldsValue: mocks.getFieldsValue,
          resetFields: mocks.resetFields,
          validateFields: mocks.validateFields,
        },
      ],
    },
    Progress: () => React.createElement("div", { "data-testid": "progress" }),
    Layout,
    Upload: {
      Dragger,
      LIST_IGNORE: Symbol("LIST_IGNORE"),
    },
  };
});

vi.mock("@ant-design/icons", async () => {
  const React = await import("react");
  return {
    InboxOutlined: () =>
      React.createElement("span", { "aria-hidden": "true" }),
  };
});

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

vi.mock("@/lib/api", () => ({
  apiService: {
    subscribeReportJob: vi.fn(),
    uploadReport: vi.fn(),
    uploadReportBatch: vi.fn(),
  },
}));

vi.mock("@/store/useFileStore", () => ({
  useFileStore: {
    getState: () => ({ loadFilesFromBackend: vi.fn() }),
  },
}));

vi.mock("../UploadOptionsModal", () => ({
  default: () => null,
}));

describe("MainContent upload layout", () => {
  beforeEach(() => {
    mocks.getFieldsValue.mockReturnValue({});
  });

  it("keeps the upload surface full width without a responsive standards column", () => {
    render(<MainContent uploadMode="single" />);

    const layout = screen.getByTestId("upload-framework-layout");
    const classTokens = layout.className.split(/\s+/);

    expect(classTokens.some((token) => token.startsWith("lg:grid-cols-"))).toBe(false);
    expect(Array.from(layout.children)).toEqual([
      screen.getByTestId("upload-dropzone-region"),
    ]);
    expect(screen.queryByTestId("standards-library")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Standards Library" })).not.toBeInTheDocument();
  });

  it("keeps compact horizontal gutters around the full-width upload area", () => {
    render(<MainContent uploadMode="single" />);

    const layout = screen.getByTestId("upload-framework-layout");
    const dropzoneRegion = screen.getByTestId("upload-dropzone-region");
    const uploadArea = screen.getByTestId("upload-area-layout");

    expect(dropzoneRegion.tagName).toBe("SECTION");
    expect(dropzoneRegion).toHaveClass(
      "flex",
      "h-full",
      "min-w-0",
      "px-2",
      "py-4",
      "sm:px-3",
      "sm:py-5",
    );
    expect(dropzoneRegion).not.toHaveClass("p-4", "sm:p-5");
    expect(uploadArea).toHaveStyle({
      margin: "0",
      padding: "0 4px 12px",
    });
    expect(dropzoneRegion).toContainElement(uploadArea);
    expect(Array.from(layout.children)).toEqual([dropzoneRegion]);
  });
});
