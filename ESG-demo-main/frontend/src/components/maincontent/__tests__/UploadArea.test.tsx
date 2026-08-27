import { render, screen } from "@testing-library/react";
import type { UploadFile } from "antd/es/upload/interface";
import { beforeEach, describe, expect, it, vi } from "vitest";

import UploadArea from "../UploadArea";

const mocks = vi.hoisted(() => ({
  draggerProps: undefined as Record<string, any> | undefined,
  listIgnore: Symbol("LIST_IGNORE"),
}));

vi.mock("antd", async () => {
  const React = await import("react");

  const Content = ({ children, style }: Record<string, any>) =>
    React.createElement(
      "div",
      { "data-testid": "mock-upload-content", style },
      children,
    );
  const Layout = Object.assign(
    ({ children, style }: Record<string, any>) =>
      React.createElement(
        "div",
        { "data-testid": "mock-upload-layout", style },
        children,
      ),
    { Content },
  );

  const Dragger = ({ children, ...props }: Record<string, any>) => {
    mocks.draggerProps = props;
    return React.createElement(
      "div",
      {
        className: props.className,
        "data-testid": "mock-upload-dragger",
        style: props.style,
      },
      children,
    );
  };

  return {
    Layout,
    Upload: {
      Dragger,
      LIST_IGNORE: mocks.listIgnore,
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

function uploadFile(uid: string, name: string): UploadFile {
  return { uid, name } as UploadFile;
}

describe("UploadArea", () => {
  beforeEach(() => {
    mocks.draggerProps = undefined;
  });

  it("accepts PDFs without showing Ant Design's duplicate upload list", () => {
    const { rerender } = render(
      <UploadArea onBeforeUpload={vi.fn()} uploadMode="single" />,
    );

    expect(mocks.draggerProps).toMatchObject({
      accept: ".pdf,application/pdf",
      multiple: false,
      name: "file",
      showUploadList: false,
    });

    rerender(<UploadArea onBeforeUpload={vi.fn()} uploadMode="multi" />);
    expect(mocks.draggerProps?.multiple).toBe(true);
  });

  it("submits one file-list batch once and suppresses Ant Design auto-upload", () => {
    const onBeforeUpload = vi.fn();
    render(<UploadArea onBeforeUpload={onBeforeUpload} uploadMode="multi" />);

    const first = uploadFile("1", "first.pdf");
    const second = uploadFile("2", "second.pdf");
    const fileList = [first, second];
    const beforeUpload = mocks.draggerProps?.beforeUpload;

    expect(beforeUpload).toBeTypeOf("function");
    expect(beforeUpload(first, fileList)).toBe(mocks.listIgnore);
    expect(beforeUpload(second, fileList)).toBe(mocks.listIgnore);
    expect(beforeUpload(first, fileList)).toBe(mocks.listIgnore);
    expect(onBeforeUpload).toHaveBeenCalledTimes(1);
    expect(onBeforeUpload).toHaveBeenCalledWith(fileList);
  });

  it("restores the original upload-card structure and visual contract", () => {
    render(<UploadArea onBeforeUpload={vi.fn()} uploadMode="single" />);

    const layout = screen.getByTestId("mock-upload-layout");
    const content = screen.getByTestId("mock-upload-content");
    const dragger = screen.getByTestId("mock-upload-dragger");

    expect(layout).toContainElement(content);
    expect(content).toContainElement(dragger);
    expect(layout).toHaveStyle({
      margin: "0",
      padding: "0 4px 12px",
      background: "#fff",
      borderRadius: "10px",
    });
    expect(content).toHaveStyle({
      padding: "12px 4px",
      minHeight: "180px",
      background: "#fff",
      borderRadius: "8px",
    });
    expect(dragger).toHaveStyle({ padding: "20px 0" });
    expect(document.querySelector(".ant-upload-drag-icon")).toBeInTheDocument();
    expect(screen.getByText("upload.draggerText")).toHaveClass("ant-upload-text");
  });

  it.each(["single", "multi"] as const)(
    "does not show the %s report mode inside the upload control",
    (uploadMode) => {
      render(<UploadArea onBeforeUpload={vi.fn()} uploadMode={uploadMode} />);

      expect(screen.getByText("upload.draggerText")).toBeVisible();
      expect(screen.queryByText("upload.singleReport")).not.toBeInTheDocument();
      expect(screen.queryByText("upload.multiReport")).not.toBeInTheDocument();
      expect(screen.queryByText(/upload\.(single|multi)Report/)).not.toBeInTheDocument();
    },
  );
});
