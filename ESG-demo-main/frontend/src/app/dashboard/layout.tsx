// app/dashboard/layout.tsx
"use client";

import React, { useEffect } from "react";
import { Layout } from "antd";
import Nav from "@/components/navbar/Nav";

const { Content } = Layout;

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  useEffect(() => {
    const rootLayout = document.getElementById("root-layout");
    if (rootLayout) {
      const timer = setTimeout(() => {
        rootLayout.classList.add("loaded");
      }, 100);
      return () => {
        clearTimeout(timer);
        rootLayout.classList.remove("loaded");
      };
    }
  }, []);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Nav />
      <Content style={{ display: "flex" }}>{children}</Content>
    </Layout>
  );
}
