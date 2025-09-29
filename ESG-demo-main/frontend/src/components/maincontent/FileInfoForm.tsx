import React from "react";
import { Form, Select, Space } from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import type { FormInstance } from "antd/es/form";
import { industries } from "@/data/industries";

interface FileInfoFormProps {
  form: FormInstance<{
    category: string;
    description: string;
    tags: string[];
    industry: string;
    semiIndustry: string;
    framework: string;
  }>;
  selectedUploadFile: UploadFile | null;
  selectedIndustry: string;
  onIndustryChange: (value: string) => void;
}

const FileInfoForm: React.FC<FileInfoFormProps> = ({
  form,
  selectedUploadFile,
  selectedIndustry,
  onIndustryChange,
}) => {
  const framework = Form.useWatch("framework", form);
  const isSASBSelected = framework === "SASB";

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{
        category: "document",
        tags: [],
        industry: "",
        semiIndustry: "",
        framework: "",
      }}>
      <Form.Item label="File Information">
        <Space direction="vertical" style={{ width: "100%" }}>
          <p>Name: {selectedUploadFile?.name}</p>
          <p>
            Size:{" "}
            {selectedUploadFile?.size
              ? (selectedUploadFile.size / 1024).toFixed(2)
              : 0}{" "}
            KB
          </p>
          <p>Type: {selectedUploadFile?.type || "Unknown"}</p>
        </Space>
      </Form.Item>

      <Form.Item
        name="framework"
        label="Framework"
        rules={[{ required: true, message: "Please select a framework" }]}>
        <Select
          placeholder="Select framework"
          options={[
            { label: "SASB", value: "SASB" },
            { label: "GRI", value: "GRI" },
            { label: "TCFD", value: "TCFD" },
          ]}
          onChange={(value) => {
            if (value !== "SASB") {
              form.setFieldsValue({
                industry: undefined,
                semiIndustry: undefined,
              });
            }
          }}
        />
      </Form.Item>

      <Form.Item
        name="industry"
        label="Industry"
        rules={[
          { required: isSASBSelected, message: "Please select an industry" },
        ]}>
        <Select
          placeholder="Select industry"
          disabled={!isSASBSelected}
          onChange={(value) => {
            onIndustryChange(value);
            form.setFieldsValue({ semiIndustry: undefined });
          }}>
          {Object.keys(industries).map((industry) => (
            <Select.Option key={industry} value={industry}>
              {industry}
            </Select.Option>
          ))}
        </Select>
      </Form.Item>

      <Form.Item
        name="semiIndustry"
        label="Sub Industry"
        rules={[
          {
            required: isSASBSelected,
            message: "Please select a Sub Industry",
          },
        ]}>
        <Select
          placeholder="Select Sub Industry"
          disabled={!isSASBSelected || !selectedIndustry}>
          {selectedIndustry &&
            industries[selectedIndustry].map((semiIndustry) => (
              <Select.Option key={semiIndustry} value={semiIndustry}>
                {semiIndustry}
              </Select.Option>
            ))}
        </Select>
      </Form.Item>
    </Form>
  );
};

export default FileInfoForm;
