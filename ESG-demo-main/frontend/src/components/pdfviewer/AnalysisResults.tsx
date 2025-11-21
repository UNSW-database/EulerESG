import React, { useMemo, useEffect, useState } from "react";
import { Table, Tag, Tooltip, Popover, Spin, Alert } from "antd";
import type { ColumnsType } from "antd/es/table";
import { InfoCircleOutlined } from "@ant-design/icons";
import { useFileStore } from "@/store/useFileStore";
import { apiService } from "@/lib/api";

type AnalysisDataItem = {
  metric_id: string;
  metric_name: string;
  disclosure_status: 'fully_disclosed' | 'partially_disclosed' | 'not_disclosed';
  reasoning: string;
  unit?: string;
  category?: string;
  topic?: string;
  type?: string;
  value?: string | number | null;
  page?: string | number | null;
  context?: string | null;
};

interface AnalysisResultsProps {
  fileId?: string;
  onPageNavigate?: (page: number) => void;
}

const AnalysisResults: React.FC<AnalysisResultsProps> = ({
  fileId,
  onPageNavigate,
}) => {
  const files = useFileStore((state) => state.files);
  const currentFile = files.find((file) => file.file_id === fileId);
  const industry = currentFile?.industry;
  const semiIndustry = currentFile?.semiIndustry;
  
  const [analysisData, setAnalysisData] = useState<AnalysisDataItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchAnalysisData = async () => {
      if (!currentFile?.file_id) {
        setError("No file selected");
        return;
      }
      
      setLoading(true);
      setError(null);
      try {
        // First try to get by file_id, if failed then get latest assessment results
        let assessment;
        try {
          assessment = await apiService.getAssessmentByFile(currentFile.file_id);
        } catch (fileIdError) {
          console.log('Failed to get assessment by file_id, trying latest:', fileIdError);
          try {
            assessment = await apiService.getLatestAssessment();
          } catch (latestError) {
            console.error('Failed to get latest assessment:', latestError);
            throw new Error('Failed to fetch assessment data from all sources');
          }
        }
        
        if (assessment && assessment.metric_analyses) {
          console.log('Raw assessment analyses:', assessment);
          console.log('Raw assessment metric analyses:', assessment.metric_analyses);
          // Convert backend data format to frontend format
          // Filter out metrics with missing required fields (indicates data corruption)
          const convertedData: AnalysisDataItem[] = assessment.metric_analyses
            .filter((item: any) => {
              if (!item.metric_id || !item.metric_name) {
                console.warn('Skipping metric with missing required fields:', item);
                return false;
              }
              return true;
            })
            .map((item: any) => ({
              metric_id: item.metric_id,
              metric_name: item.metric_name,
              disclosure_status: item.disclosure_status,
              reasoning: item.reasoning || '',
              unit: item.unit || '',
              category: item.category || '',
              topic: item.topic || '',
              type: item.type || '',
              value: item.value || null,
              page: item.page || null,
              context: item.context || null
            }));
          console.log('Converted analysis data:', convertedData);
          setAnalysisData(convertedData);
        } else {
          setAnalysisData([]);
        }
      } catch (err) {
        console.error('Failed to fetch assessment data:', err);
        // Check if it's a 404 (no assessment available)
        if (err && typeof err === 'object' && 'message' in err && err.message.includes('404')) {
          setError('No analysis results available. Please upload a report and run compliance analysis first.');
        } else {
          setError('Failed to load analysis data. Please ensure the report has been processed and analysis has been completed.');
        }
        setAnalysisData([]);
      } finally {
        setLoading(false);
      }
    };

    fetchAnalysisData();
  }, [currentFile?.file_id, fileId]);

  const getCategoryColor = (category: string) => {
    switch (category) {
      case "Quantitative":
        return "blue";
      case "Discussion and Analysis":
        return "purple";
      default:
        return "default";
    }
  };

  const data = analysisData.map((item, index) => ({
    ...item,
    key: `${item.metric_id}-${index}`,
  }));

  const columns: ColumnsType<AnalysisDataItem> = useMemo(
    () => [
      {
        title: "Metric",
        dataIndex: "metric_name",
        key: "metric_name",
        width: 200,
      },
      {
        title: "Status",
        dataIndex: "disclosure_status",
        key: "disclosure_status",
        width: 150,
        render: (status: string) => {
          let color = "default";
          let text = status;
          if (status === "fully_disclosed") {
            color = "success";
            text = "Fully Disclosed";
          } else if (status === "partially_disclosed") {
            color = "warning";
            text = "Partially Disclosed";
          } else if (status === "not_disclosed") {
            color = "error";
            text = "Not Disclosed";
          }
          return <Tag color={color}>{text}</Tag>;
        },
        filters: [
          { text: "Fully Disclosed", value: "fully_disclosed" },
          { text: "Partially Disclosed", value: "partially_disclosed" },
          { text: "Not Disclosed", value: "not_disclosed" },
        ],
        onFilter: (value, record) => record.disclosure_status === value,
      },
      {
        title: "LLM Analysis",
        dataIndex: "reasoning",
        key: "reasoning",
        width: 300,
        render: (reasoning: string, record: AnalysisDataItem) => (
          <div className="flex items-center gap-1">
            <div className="max-w-xs truncate" title={reasoning}>
              {reasoning}
            </div>
            {(reasoning || record.context) && (
              <Popover
                content={
                  <div className="max-w-md p-2">
                    <h4 className="font-semibold mb-2">LLM Analysis:</h4>
                    <p className="text-sm mb-2">{reasoning}</p>
                    {record.context && (
                      <>
                        <h4 className="font-semibold mb-1">Context:</h4>
                        <p className="text-sm mb-2">{record.context}</p>
                      </>
                    )}
                    {record.page && (
                      <p className="text-sm mt-2 text-blue-500">
                        Page: {record.page}
                      </p>
                    )}
                  </div>
                }
                title="Analysis Details"
                trigger="click">
                <Tooltip title="Click to view full analysis and context">
                  <InfoCircleOutlined
                    className="text-blue-500 cursor-pointer hover:text-blue-700"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (record.page && onPageNavigate) {
                        const pageNumber =
                          typeof record.page === "string"
                            ? parseInt(record.page.toString().split(",")[0])
                            : record.page;
                        if (!isNaN(pageNumber)) {
                          onPageNavigate(pageNumber);
                        }
                      }
                    }}
                  />
                </Tooltip>
              </Popover>
            )}
          </div>
        ),
      },
      {
        title: "Category",
        dataIndex: "category",
        key: "category",
        width: 120,
        render: (category: string) => (
          <Tag color={getCategoryColor(category)}>{category}</Tag>
        ),
      },
      { 
        title: "Unit", 
        dataIndex: "unit", 
        key: "unit", 
        width: 100 
      },
      {
        title: "Type",
        dataIndex: "type",
        key: "type",
        width: 150,
      },
      {
        title: "Value",
        dataIndex: "value",
        key: "value",
        width: 120,
        render: (value: string | number | null) => (
          <span>{value || 'Not specified'}</span>
        ),
      },
      {
        title: "Page",
        dataIndex: "page",
        key: "page",
        width: 80,
        render: (page: string | number | null, record: AnalysisDataItem) => (
          <span 
            className={page ? "text-blue-500 cursor-pointer hover:underline" : "text-gray-400"}
            onClick={() => {
              if (page && onPageNavigate) {
                const pageNumber = typeof page === "string" 
                  ? parseInt(page.toString().split(",")[0]) 
                  : page;
                if (!isNaN(pageNumber)) {
                  onPageNavigate(pageNumber);
                }
              }
            }}
          >
            {page || 'N/A'}
          </span>
        ),
      },
    ],
    [data, onPageNavigate]
  );

  const getAnalysisSummary = (data: AnalysisDataItem[]) => {
    // Since backend doesn't distinguish between disclosure and activity metrics,
    // we'll only show disclosure metrics to avoid empty activity section
    const disclosureData = data;
    
    const getStats = (group: AnalysisDataItem[]) => {
      const red = group.filter(
        (item) => item.disclosure_status === "not_disclosed"
      ).length;
      const yellow = group.filter(
        (item) => item.disclosure_status === "partially_disclosed"
      ).length;
      const green = group.filter(
        (item) => item.disclosure_status === "fully_disclosed"
      ).length;
      return { red, yellow, green };
    };

    return {
      disclosure: getStats(disclosureData),
      // Remove activity section since backend doesn't provide this distinction
    };
  };

  const summary = getAnalysisSummary(data);
  // console.log("data.length", summary.disclosure);

  if (loading) {
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-2xl font-bold text-gray-800 !my-0">
          {currentFile?.name} ({currentFile?.framework})
        </h1>
        <h2 className="text-xl font-semibold text-gray-800 !my-0">
          {industry && semiIndustry
            ? `${industry} - ${semiIndustry}`
            : "Industry Analysis"}
        </h2>
        <div className="bg-white rounded-lg shadow-sm p-6 text-center">
          <Spin size="large" />
          <p className="mt-4 text-gray-600">Loading analysis results...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-2xl font-bold text-gray-800 !my-0">
          {currentFile?.name} ({currentFile?.framework})
        </h1>
        <h2 className="text-xl font-semibold text-gray-800 !my-0">
          {industry && semiIndustry
            ? `${industry} - ${semiIndustry}`
            : "Industry Analysis"}
        </h2>
        <Alert
          message="Error"
          description={error}
          type="error"
          showIcon
          action={
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600">
              Retry
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-gray-800 !my-0">
        {currentFile?.name} ({currentFile?.framework})
      </h1>
      <h2 className="text-xl font-semibold text-gray-800 !my-0">
        {industry && semiIndustry
          ? `${industry} - ${semiIndustry}`
          : "Industry Analysis"}
      </h2>
      <div className="bg-white rounded-lg shadow-sm p-6 hover:scale-[1.02] hover:shadow-lg transition-transform duration-300">
        <h3 className="text-xl font-semibold mb-6 text-gray-800">
          Analysis Summary
        </h3>
        <div className="flex flex-col gap-8">
          {summary.disclosure && (() => {
            const group = summary.disclosure;
            const total = group.red + group.yellow + group.green;
            
            // Only render if we have data
            if (total === 0) {
              return null;
            }
            
            const redPct = ((group.red / total) * 100).toFixed(1);
            const yellowPct = ((group.yellow / total) * 100).toFixed(1);
            const greenPct = ((group.green / total) * 100).toFixed(1);

            return (
              <div key="disclosure">
                <h4 className="text-lg font-semibold text-start text-black mb-4">
                  Disclosure Topics & Metrics
                </h4>
                <div className="flex flex-wrap gap-4">
                  {[
                    {
                      color: "text-red-500",
                      value: group.red,
                      percent: redPct,
                      label: "Not Disclosed/Discussed",
                    },
                    {
                      color: "text-yellow-500",
                      value: group.yellow,
                      percent: yellowPct,
                      label: "Disclosed/Discussed But Not Clear",
                    },
                    {
                      color: "text-green-500",
                      value: group.green,
                      percent: greenPct,
                      label: "Disclosed/Discussed",
                    },
                  ].map((item) => (
                    <div
                      className="flex-1 min-w-[200px] flex flex-col items-center gap-2"
                      key={item.label}>
                      <div className={`text-4xl font-bold ${item.color}`}>
                        {item.percent}%
                      </div>
                      <div className="text-lg text-gray-600">
                        ({item.value}/{total})
                      </div>
                      <div className="mt-1 text-md text-center font-semibold">
                        {item.label}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}
        </div>
      </div>
      <div className="bg-white rounded-lg shadow-sm p-4 hover:scale-[1.01] hover:shadow-lg transition-transform duration-300">
        <h3 className="text-lg font-semibold mb-3 text-gray-800">
          Analysis Results
        </h3>
        <Table
          columns={columns}
          dataSource={data}
          className="w-full"
          scroll={{ x: "max-content", y: 300 }}
          pagination={false}
          rowKey="key"
        />
      </div>
    </div>
  );
};

export default AnalysisResults;
