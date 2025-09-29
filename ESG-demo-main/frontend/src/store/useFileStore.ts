// src/store/useFileStore.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { apiService } from "@/lib/api";

export interface File {
  key: string;
  name: string;
  size: string;
  dateUploaded: string;
  type: string;
  tableStatus: string;
  imageStatus: string;
  status: "pending" | "ready" | "failed";
  url?: string;
  industry?: string;
  semiIndustry?: string;
  framework?: string;
  file_id?: string;
  backend_status?: string;
}

interface FileStore {
  files: File[];
  selectedSemiIndustry: string | null;
  loading: boolean;
  lastRefresh: number;
  addFile: (file: File) => void;
  deleteFile: (key: string) => Promise<void>;
  updateFileStatus: (key: string, status: "pending" | "ready" | "failed") => void;
  updateFilePages: (key: string, pages: number) => void;
  setSelectedSemiIndustry: (semiIndustry: string | null) => void;
  loadFilesFromBackend: () => Promise<void>;
  setLoading: (loading: boolean) => void;
}

export const useFileStore = create<FileStore>()(
  persist(
    (set, get) => ({
      files: [],
      selectedSemiIndustry: null,
      loading: false,
      lastRefresh: 0,
      setLoading: (loading) => set({ loading }),
      addFile: (file) =>
        set((state) => ({
          files: [...state.files, { ...file, status: "pending" }],
        })),
      updateFileStatus: (key, status) =>
        set((state) => ({
          files: state.files.map((file) =>
            file.key === key ? { ...file, status } : file
          ),
        })),
      updateFilePages: (key, pages) =>
        set((state) => ({
          files: state.files.map((file) =>
            file.key === key ? { ...file, pages: pages.toString() } : file
          ),
        })),
      deleteFile: async (key) => {
        const file = get().files.find(f => f.key === key);
        if (file?.file_id) {
          try {
            await apiService.deleteFile(file.file_id);
          } catch (error) {
            console.error('Failed to delete file from backend:', error);
          }
        }
        set((state) => ({
          files: state.files.filter((file) => file.key !== key),
        }));
      },
      setSelectedSemiIndustry: (semiIndustry) =>
        set(() => ({
          selectedSemiIndustry: semiIndustry,
        })),
      loadFilesFromBackend: async () => {
        try {
          set({ loading: true });
          console.log('Loading files from backend...');
          const response = await apiService.getFiles();
          console.log('Backend response:', response);
          if (response.status === 'success') {
            const backendFiles = response.files.map((file: any) => {
              console.log('Mapping file:', file);
              return {
                key: file.file_id,
                name: file.original_name,
                size: `${(file.file_size / 1024).toFixed(2)} KB`,
                dateUploaded: file.upload_time.split('T')[0],
                type: file.original_name.split('.').pop()?.toUpperCase() || 'Unknown',
                tableStatus: file.status === 'processed' ? 'Ready' : 
                            file.status === 'failed' ? 'Failed' : 'Pending',
                imageStatus: file.status === 'processed' ? 'Ready' : 
                            file.status === 'failed' ? 'Failed' : 'Pending', 
                status: file.status === 'processed' ? 'ready' as const : 
                       file.status === 'failed' ? 'failed' as const : 'pending' as const,
                file_id: file.file_id,
                backend_status: file.status,
                industry: file.industry || 'Unknown',
                semiIndustry: file.semi_industry || 'Unknown',
                pages: file.total_pages?.toString() || '-', 
                framework: file.framework || 'SASB'
              };
            });
            console.log('Mapped files:', backendFiles);
            
            // 合并现有的前端文件和后端文件
            // 保留前端添加的文件（可能还在上传中），更新已有的后端文件
            set((state) => {
              const existingFiles = state.files;
              const backendFileIds = new Set(backendFiles.map(f => f.key));
              
              // 保留前端文件中不在后端的文件（上传中的文件）
              const frontendOnlyFiles = existingFiles.filter(f => !backendFileIds.has(f.key));
              
              // 合并文件列表
              const mergedFiles = [...backendFiles, ...frontendOnlyFiles];
              
              // 检测是否有变化
              const hasChanges = 
                state.files.length !== mergedFiles.length ||
                mergedFiles.some(newFile => {
                  const existingFile = state.files.find(f => f.key === newFile.key);
                  return !existingFile || 
                         existingFile.status !== newFile.status ||
                         existingFile.backend_status !== newFile.backend_status;
                });
              
              if (hasChanges) {
                console.log('🔄 File list updated - changes detected');
              }
              
              return { 
                files: mergedFiles,
                lastRefresh: Date.now()
              };
            });
          }
        } catch (error) {
          console.error('Failed to load files from backend:', error);
        } finally {
          set({ loading: false });
        }
      }
    }),
    {
      name: "file-storage", // unique name for localStorage key
      partialize: (state) => ({
        selectedSemiIndustry: state.selectedSemiIndustry,
      }),
    }
  )
);
