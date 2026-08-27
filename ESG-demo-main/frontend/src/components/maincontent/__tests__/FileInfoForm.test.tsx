import { Form } from "antd";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import FileInfoForm, { type FileInfoFormValues } from "../FileInfoForm";

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

vi.mock("@/lib/api", () => ({
  apiService: {
    getCompanies: vi.fn().mockResolvedValue({ companies: [] }),
    getGriOptions: vi.fn().mockResolvedValue({
      sectors: [],
      topicsBySector: {},
    }),
  },
}));

function FormHarness({ onRead }: { onRead: (values: FileInfoFormValues) => void }) {
  const [form] = Form.useForm<FileInfoFormValues>();

  return (
    <>
      <FileInfoForm
        form={form}
        selectedUploadFiles={[]}
        selectedIndustry=""
        onIndustryChange={vi.fn()}
        uploadMode="single"
      />
      <button type="button" onClick={() => onRead(form.getFieldsValue(true))}>
        Read form
      </button>
    </>
  );
}

describe("FileInfoForm framework selection", () => {
  it("offers plain enabled AASB and selects it without framework-specific scope fields", async () => {
    const user = userEvent.setup();
    const onRead = vi.fn();
    render(<FormHarness onRead={onRead} />);

    await user.click(screen.getByLabelText("upload.framework"));
    const aasbOption = await screen.findByText("AASB");
    expect(aasbOption).toBeInTheDocument();
    expect(aasbOption.closest("[aria-disabled='true']")).toBeNull();
    expect(screen.queryByText(/temporarily unavailable|coming soon|暂未开放/i)).not.toBeInTheDocument();

    await user.click(aasbOption);
    await user.click(screen.getByRole("button", { name: "Read form" }));

    await waitFor(() => {
      expect(onRead).toHaveBeenCalledWith(
        expect.objectContaining({ framework: "AASB" }),
      );
    });
    expect(screen.queryByLabelText("upload.industry")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("upload.subIndustry")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("upload.cdpTopic")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("upload.griSector")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("upload.griTopics")).not.toBeInTheDocument();
  });
});
