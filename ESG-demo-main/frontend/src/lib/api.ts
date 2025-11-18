/**
 * ESG Backend API Service
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

export interface UploadResponse {
  status: string;
  report_id: string;
  summary: string;
}

export interface MetricsUploadResponse {
  status: string;
  collection_id: string;
  metrics_count: number;
}

export interface ChatRequest {
  message: string;
  include_context?: boolean;
  session_id?: string;
}

export interface ChatResponse {
  session_id: string;
  response: string;
  relevant_segments?: string[];
}

export interface ComplianceAnalysisResponse {
  status: string;
  assessment: {
    report_id: string;
    total_metrics: number;
    overall_score: number;
    disclosure_summary: {
      fully_disclosed: number;
      partially_disclosed: number;
      not_disclosed: number;
    };
    report_path: string;
  };
}

export interface SystemStatus {
  status: string;
  components: {
    report_loaded: boolean;
    metrics_loaded: boolean;
    assessment_available: boolean;
    llm_configured: boolean;
  };
  report_info?: {
    document_id: string;
    segments_count: number;
  };
  metrics_info?: {
    collection_id: string;
    metrics_count: number;
  };
}

class APIService {
  private async fetchWithError(url: string, options?: RequestInit) {
    const response = await fetch(url, options);
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(errorData.detail || `HTTP ${response.status}`);
    }
    return response.json();
  }

  // Get system status
  async getSystemStatus(): Promise<SystemStatus> {
    return this.fetchWithError(`${API_BASE_URL}/api/system/status`);
  }

  // Get file list
  async getFiles(fileType?: string, status?: string) {
    const params = new URLSearchParams();
    if (fileType) params.append('file_type', fileType);
    if (status) params.append('status', status);
    
    const url = `${API_BASE_URL}/api/files${params.toString() ? '?' + params.toString() : ''}`;
    return this.fetchWithError(url);
  }

  // Delete file
  async deleteFile(fileId: string) {
    console.log(`Calling DELETE ${API_BASE_URL}/api/files/${fileId}`);
    const result = await this.fetchWithError(`${API_BASE_URL}/api/files/${fileId}`, {
      method: 'DELETE'
    });
    console.log('Delete API response:', result);
    return result;
  }

  // Upload PDF report
  async uploadReport(
    file: File, 
    framework?: string, 
    industry?: string, 
    semiIndustry?: string
  ): Promise<UploadResponse> {
    const formData = new FormData();
    formData.append('file', file);
    
    // Add framework and industry selection information
    if (framework) formData.append('framework', framework);
    if (industry) formData.append('industry', industry);
    if (semiIndustry) formData.append('semiIndustry', semiIndustry);

    return this.fetchWithError(`${API_BASE_URL}/api/upload-report`, {
      method: 'POST',
      body: formData,
    });
  }

  // Upload ESG metrics - REMOVED: This function was never used and had misleading logic
  // that allowed uploading without a file (using "default metrics"), which could mask errors

  // Execute compliance analysis - REMOVED: This function was never called by the frontend

  // Send chat message
  async sendMessage(request: ChatRequest): Promise<ChatResponse> {
    return this.fetchWithError(`${API_BASE_URL}/api/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    });
  }

  // 获取详细评估结果
  async getAssessment() {
    return this.fetchWithError(`${API_BASE_URL}/api/assessment`);
  }

  // 根据文件ID获取评估结果
  async getAssessmentByFile(fileId: string) {
    return this.fetchWithError(`${API_BASE_URL}/api/assessment/${fileId}`);
  }

  // 获取最新的评估结果
  async getLatestAssessment() {
    return this.fetchWithError(`${API_BASE_URL}/api/assessment/latest`);
  }

  // 获取聊天历史 - REMOVED: This function was never called by the frontend

  // 获取最新的合规报告
  async getLatestReport() {
    return this.fetchWithError(`${API_BASE_URL}/api/reports/latest`);
  }

  // 根据文件ID获取合规报告
  async getReportByFileId(fileId: string) {
    return this.fetchWithError(`${API_BASE_URL}/api/reports/${fileId}`);
  }
}

export const apiService = new APIService();