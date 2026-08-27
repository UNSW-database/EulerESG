// app/dashboard/layout.tsx
"use client";

import React, { Suspense, useEffect } from "react";
import dynamic from "next/dynamic";
import { Layout } from "antd";
import DashboardSidebar from "@/components/navbar/DashboardSidebar";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AUTH_TOKEN_KEY } from "@/lib/auth";
import { AntdRegistry } from "@/lib/antd";
import { useFileStore } from "@/store/useFileStore";

const { Content } = Layout;

const FloatingChatAssistant = dynamic(
  () => import("@/components/cross-analysis/FloatingChatAssistant"),
  { ssr: false },
);

function DashboardAssistant() {
  const pathname = usePathname() || "";
  const searchParams = useSearchParams();
  const selectedFileId = useFileStore((state) => state.selectedFileId);
  const isComplianceRoute = pathname.startsWith("/dashboard/chat");
  const reportFileId = isComplianceRoute
    ? searchParams.get("file_id") || selectedFileId || undefined
    : undefined;

  return (
    <FloatingChatAssistant
      conversationKey={reportFileId ? `file:${reportFileId}` : "general"}
      fileId={reportFileId}
      includeContext={Boolean(reportFileId)}
    />
  );
}

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem(AUTH_TOKEN_KEY) : null;
    if (!token) {
      router.replace("/login");
    }
  }, [router]);

  return (
    <AntdRegistry>
      <Layout
        data-dashboard-shell
        style={{ minHeight: "100vh", flexDirection: "row" }}
      >
        <Suspense
          fallback={<div aria-hidden="true" className="h-screen w-[260px] shrink-0 bg-[#f9f9f9]" />}
        >
          <DashboardSidebar />
        </Suspense>
        <Content style={{ display: "flex", minWidth: 0 }}>{children}</Content>
        <Suspense fallback={null}>
          <DashboardAssistant />
        </Suspense>
      </Layout>
    </AntdRegistry>
  );
}
