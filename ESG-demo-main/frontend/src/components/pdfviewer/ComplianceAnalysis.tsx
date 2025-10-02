import React, { useState } from 'react';
import { Card, Button, Spin, Alert, message } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { apiService } from '@/lib/api';
import type { File } from '@/store/useFileStore';

interface ComplianceAnalysisProps {
  analysisFile?: File;
  onAnalysisComplete?: () => void;
}


const ComplianceAnalysis: React.FC<ComplianceAnalysisProps> = ({ analysisFile, onAnalysisComplete }) => {
  const [loading, setLoading] = useState(false);
  const [markdownContent, setMarkdownContent] = useState<string | null>(null);
  const [reportFile, setReportFile] = useState<string | null>(null);

  const handleStartAnalysis = async () => {
    if (!analysisFile?.file_id) {
      message.error('No file selected for analysis');
      return;
    }

    setLoading(true);
    try {
      message.loading(`Loading compliance analysis report for ${analysisFile.name}...`, 0);

      // Use file-specific report instead of latest
      const reportData = await apiService.getReportByFileId(analysisFile.file_id);
      setMarkdownContent(reportData.content);
      setReportFile(reportData.report_file);

      message.destroy();
      message.success('Report loaded successfully!');
    } catch (error) {
      message.destroy();
      message.error(`Failed to load report: ${error instanceof Error ? error.message : String(error)}`);
      console.error('Report loading error:', error);
    } finally {
      setLoading(false);
    }
  };


  if (!markdownContent && !loading) {
    return (
      <Card>
        <div style={{ textAlign: 'center', padding: '40px' }}>
          <FileTextOutlined style={{ fontSize: '48px', color: '#1890ff', marginBottom: '16px' }} />
          <h3>ESG Compliance Analysis Report</h3>
          <p style={{ color: '#666', marginBottom: '24px' }}>
            View the latest Euler AI compliance analysis report
          </p>
          <Button type="primary" size="large" onClick={handleStartAnalysis}>
            Load Latest Report
          </Button>
        </div>
      </Card>
    );
  }

  if (loading) {
    return (
      <Card>
        <div style={{ textAlign: 'center', padding: '40px' }}>
          <Spin size="large" />
          <h3 style={{ marginTop: '16px' }}>Loading Report...</h3>
          <p style={{ color: '#666' }}>Loading the latest Euler AI compliance analysis report, please wait...</p>
        </div>
      </Card>
    );
  }

  return (
    <div style={{ padding: '16px' }}>
      <Alert
        message="Compliance Analysis Report Loaded"
        description={reportFile ? `Displaying report: ${reportFile}` : 'Latest Euler AI compliance analysis report'}
        type="success"
        showIcon
        style={{ marginBottom: '16px' }}
      />

      <Card 
        title="ESG Compliance Analysis Report"
        extra={
          <Button type="primary" onClick={handleStartAnalysis} loading={loading}>
            Refresh Report
          </Button>
        }
        style={{ marginBottom: '16px' }}
      >
        <div style={{ 
          maxHeight: '700px', 
          overflowY: 'auto', 
          backgroundColor: '#ffffff', 
          padding: '24px',
          borderRadius: '8px',
          border: '1px solid #f0f0f0',
          boxShadow: '0 2px 8px rgba(0,0,0,0.06)'
        }}>
          <ReactMarkdown 
            remarkPlugins={[remarkGfm]}
            components={{
              h1: ({children}) => <h1 style={{color: '#1890ff', borderBottom: '2px solid #1890ff', paddingBottom: '8px', marginBottom: '16px'}}>{children}</h1>,
              h2: ({children}) => <h2 style={{color: '#262626', borderBottom: '1px solid #d9d9d9', paddingBottom: '6px', marginTop: '24px', marginBottom: '12px'}}>{children}</h2>,
              h3: ({children}) => <h3 style={{color: '#595959', marginTop: '20px', marginBottom: '10px'}}>{children}</h3>,
              h4: ({children}) => <h4 style={{color: '#8c8c8c', marginTop: '16px', marginBottom: '8px'}}>{children}</h4>,
              p: ({children}) => <p style={{lineHeight: '1.6', marginBottom: '12px', color: '#595959'}}>{children}</p>,
              ul: ({children}) => <ul style={{paddingLeft: '20px', marginBottom: '12px'}}>{children}</ul>,
              li: ({children}) => <li style={{marginBottom: '4px', color: '#595959'}}>{children}</li>,
              table: ({children}) => (
                <div style={{overflowX: 'auto', marginBottom: '20px', marginTop: '16px'}}>
                  <table style={{
                    width: '100%', 
                    borderCollapse: 'collapse',
                    backgroundColor: '#ffffff',
                    border: '2px solid #f0f0f0',
                    borderRadius: '6px',
                    overflow: 'hidden'
                  }}>{children}</table>
                </div>
              ),
              thead: ({children}) => <thead style={{backgroundColor: '#fafafa'}}>{children}</thead>,
              tbody: ({children}) => <tbody>{children}</tbody>,
              th: ({children}) => <th style={{
                padding: '14px 16px', 
                backgroundColor: '#f8f9fa', 
                border: '1px solid #e9ecef',
                fontWeight: '600',
                color: '#495057',
                textAlign: 'left',
                fontSize: '14px'
              }}>{children}</th>,
              td: ({children}) => <td style={{
                padding: '12px 16px', 
                border: '1px solid #f0f0f0',
                color: '#6c757d',
                fontSize: '14px',
                backgroundColor: '#ffffff'
              }}>{children}</td>,
              tr: ({children}) => <tr style={{borderBottom: '1px solid #f0f0f0'}}>{children}</tr>,
              strong: ({children}) => <strong style={{color: '#262626', fontWeight: '600'}}>{children}</strong>,
              code: ({children}) => <code style={{
                backgroundColor: '#f5f5f5',
                padding: '2px 6px',
                borderRadius: '3px',
                fontSize: '0.9em',
                color: '#d32f2f'
              }}>{children}</code>,
              blockquote: ({children}) => <blockquote style={{
                borderLeft: '4px solid #1890ff',
                paddingLeft: '16px',
                marginLeft: '0',
                fontStyle: 'italic',
                color: '#8c8c8c',
                backgroundColor: '#f9f9f9',
                padding: '12px 16px',
                borderRadius: '0 4px 4px 0'
              }}>{children}</blockquote>
            }}
          >
            {markdownContent || ''}
          </ReactMarkdown>
        </div>
      </Card>
    </div>
  );
};

export default ComplianceAnalysis;