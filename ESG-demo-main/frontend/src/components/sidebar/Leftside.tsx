"use client";

import React, { useState } from "react";
import { Layout, Menu } from "antd";
import type { MenuProps } from "antd";
import { QuestionCircleOutlined, HomeOutlined } from "@ant-design/icons";

const { Sider } = Layout;

interface AppSidebarProps {
  onSectionChangeAction: (section: "files" | "tutorial") => void;
}

const items: MenuProps["items"] = [
  {
    key: "files",
    icon: <HomeOutlined />,
    label: "Files",
  },
  {
    key: "tutorial",
    icon: <QuestionCircleOutlined />,
    label: "Tutorial",
  },
];

export function AppSidebar({ onSectionChangeAction }: AppSidebarProps) {
  const [collapsed, setCollapsed] = useState(true);

  const handleMenuClick: MenuProps["onClick"] = ({ key }) => {
    onSectionChangeAction(key as "files" | "tutorial");
  };

  return (
    <Sider
      collapsible
      collapsed={collapsed}
      onCollapse={setCollapsed}
      width={250}
      theme="light"
      style={{ background: "#fff" }}>
      <Menu
        mode="inline"
        defaultSelectedKeys={["files"]}
        style={{ height: "100%", borderRight: 0 }}
        items={items}
        onClick={handleMenuClick}
      />
    </Sider>
  );
}
