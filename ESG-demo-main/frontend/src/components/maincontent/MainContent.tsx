"use client";

// import "@ant-design/v5-patch-for-react-19";
import React, { useState, useEffect } from "react";
import { Form, message } from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import { useFileStore } from "@/store/useFileStore";
import { apiService } from "@/lib/api";
import UploadArea from "./UploadArea";
import UploadOptionsModal from "./UploadOptionsModal";

interface UploadOptions {
  category: string;
  description: string;
  tags: string[];
  industry: string;
  semiIndustry: string;
  framework: string;
}

const MainContent = () => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedUploadFile, setSelectedUploadFile] =
    useState<UploadFile | null>(null);
  const [selectedIndustry, setSelectedIndustry] = useState<string>("");
  const [form] = Form.useForm<UploadOptions>();
  const [pendingFiles, setPendingFiles] = useState<
    { uid: string; timestamp: number }[]
  >([]);

  useEffect(() => {
    if (pendingFiles.length > 0) {
      const timer = setTimeout(() => {
        pendingFiles.forEach((file) => {
          useFileStore.getState().updateFileStatus(file.uid, "ready");
        });
        setPendingFiles([]);
      }, 5000);

      return () => {
        clearTimeout(timer);
      };
    }
  }, [pendingFiles]);

  const handleBeforeUpload = (file: UploadFile) => {
    setSelectedUploadFile(file);
    setIsModalOpen(true);
    return false;
  };

  const handleModalOk = async () => {
    try {
      await form.validateFields();
      const now = new Date();

      if (selectedUploadFile) {
        const { name, size, uid } = selectedUploadFile;
        const values = form.getFieldsValue();

        const fileExtension =
          name?.split(".").pop()?.toUpperCase() || "Unknown";

        // 显示上传进度
        void message.loading('正在上传文件...', 0);

        // 先关闭模态框
        setIsModalOpen(false);
        setSelectedUploadFile(null);
        form.resetFields();

        // 立即添加文件到前端列表（pending状态）
        const fileItem = {
          key: uid,
          name: name || "Unknown File",
          size: size ? `${(size / 1024).toFixed(2)} KB` : "Unknown",
          dateUploaded: now.toLocaleDateString(),
          type: fileExtension,
          tableStatus: "Pending",
          imageStatus: "Pending", 
          status: "pending" as const,
          pages: "-",
          industry: values.industry || "",
          semiIndustry: values.semiIndustry || "",
          framework: values.framework || "",
        };
        
        // 立即添加到store
        useFileStore.getState().addFile(fileItem);

        // 然后在后台执行上传
        setTimeout(async () => {
          try {
            // 获取文件对象
            const file = selectedUploadFile.originFileObj || selectedUploadFile;
            
            // 确保file是File对象
            if (!(file instanceof File)) {
              console.error('File object:', file);
              console.error('selectedUploadFile:', selectedUploadFile);
              throw new Error('无效的文件对象');
            }
            
            console.log('Uploading file:', file.name, 'Size:', file.size);
            console.log('Form values:', values);
            
            // 上传报告并传递行业选择信息，后端会自动使用对应的SASB指标
            const response = await apiService.uploadReport(file, values.framework, values.industry, values.semiIndustry);
            
            message.destroy(); // 销毁loading消息
            void message.success('文件上传成功！');
            
            // 更新文件状态为ready（处理中但可以显示）
            useFileStore.getState().updateFileStatus(uid, "ready");
            
            // 如果响应中包含页数信息，更新文件记录
            if (response && response.total_pages) {
              useFileStore.getState().updateFilePages(uid, response.total_pages);
            }
            
            // 从后端重新加载文件列表以获取最新状态
            await useFileStore.getState().loadFilesFromBackend();
            
          } catch (error: any) {
            message.destroy(); // 销毁loading消息
            void message.error(`上传失败: ${error.message || error}`);
            
            // 上传失败时，更新状态为failed但保留在列表中
            useFileStore.getState().updateFileStatus(uid, "failed");
            
            console.error('Upload error:', error);
            console.error('Upload details:', {
              selectedUploadFile,
              originFileObj: selectedUploadFile?.originFileObj,
              type: typeof selectedUploadFile?.originFileObj
            });
          }
        }, 100);
      } else {
        // 没有文件时也要关闭模态框
        setIsModalOpen(false);
        setSelectedUploadFile(null);
        form.resetFields();
      }
    } catch (error) {
      console.error("Validation failed:", error);
      void message.error("请填写所有必填字段");
    }
  };

  const handleModalCancel = () => {
    setIsModalOpen(false);
    setSelectedUploadFile(null);
    form.resetFields();
  };

  return (
    <>
      <UploadArea onBeforeUpload={handleBeforeUpload} />
      <UploadOptionsModal
        isOpen={isModalOpen}
        selectedUploadFile={selectedUploadFile}
        selectedIndustry={selectedIndustry}
        onOk={handleModalOk}
        onCancel={handleModalCancel}
        onIndustryChange={setSelectedIndustry}
        form={form}
      />
    </>
  );
};

export default MainContent;
