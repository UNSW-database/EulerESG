"use client";
import { useState, useEffect, useRef } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// 设置 PDF.js worker
pdfjs.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.min.js";

interface PDFChatViewerProps {
  fileUrl: string;
  targetPage?: number;
}

const PDFChatViewer: React.FC<PDFChatViewerProps> = ({
  fileUrl,
  targetPage,
}) => {
  const [numPages, setNumPages] = useState<number>();
  const [pageNumber, setPageNumber] = useState<number>(1);
  const [containerWidth, setContainerWidth] = useState<number>(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<{ [key: number]: HTMLDivElement | null }>({});

  useEffect(() => {
    if (targetPage) {
      setPageNumber(targetPage);
      // 滚动到目标页面
      const targetElement = pageRefs.current[targetPage];
      if (targetElement && containerRef.current) {
        containerRef.current.scrollTo({
          top: targetElement.offsetTop - 20, // 添加一些顶部间距
          behavior: "smooth",
        });
      }
    }
  }, [targetPage]);

  useEffect(() => {
    if (containerRef.current) {
      setContainerWidth(containerRef.current.offsetWidth);
    }
    const handleResize = () => {
      if (containerRef.current) {
        setContainerWidth(containerRef.current.offsetWidth);
      }
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  function onDocumentLoadSuccess({ numPages }: { numPages: number }): void {
    setNumPages(numPages);
  }

  return (
    <div className="flex flex-col items-center w-full">
      <div
        ref={containerRef}
        className="overflow-y-auto max-h-[800px] w-full px-2">
        <Document file={fileUrl} onLoadSuccess={onDocumentLoadSuccess}>
          {Array.from(new Array(numPages), (el, index) => (
            <div
              key={`page_${index + 1}`}
              ref={(el) => {
                pageRefs.current[index + 1] = el;
              }}>
              <Page
                pageNumber={index + 1}
                width={containerWidth > 0 ? containerWidth : undefined}
                className="mb-4"
                renderTextLayer={true}
                renderAnnotationLayer={true}
              />
            </div>
          ))}
        </Document>
      </div>
      <p className="mt-2 text-sm text-gray-600">
        Page {pageNumber} of {numPages}
      </p>
    </div>
  );
};

export default PDFChatViewer;
