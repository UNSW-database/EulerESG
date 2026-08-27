import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const globalsCss = readFileSync(resolve(process.cwd(), "src/app/globals.css"), "utf8");
const rootLayout = readFileSync(resolve(process.cwd(), "src/app/layout.tsx"), "utf8");
const dashboardLayout = readFileSync(resolve(process.cwd(), "src/app/dashboard/layout.tsx"), "utf8");
const crossAnalysisLayout = readFileSync(resolve(process.cwd(), "src/app/cross-analysis/layout.tsx"), "utf8");

describe("global vertical scrolling policy", () => {
  it("keeps the document viewport as the single page-level vertical scroll owner", () => {
    expect(globalsCss).toMatch(
      /html\s*\{[^}]*overflow-x:\s*hidden;[^}]*overflow-y:\s*auto;[^}]*overscroll-behavior-y:\s*auto;/,
    );
    expect(globalsCss).toMatch(
      /body\s*\{[^}]*overflow-x:\s*clip;[^}]*overflow-y:\s*visible;/,
    );
    expect(globalsCss).not.toContain("overscroll-behavior-y: none");
    expect(rootLayout).not.toMatch(/<(?:html|body)[^>]*overflow-x-hidden/);
  });

  it("keeps the evidence reader's horizontal overflow on html only", () => {
    expect(globalsCss).toMatch(
      /html\.evidence-x-scroll\s*\{[^}]*overflow-x:\s*auto\s*!important;/,
    );
    expect(globalsCss).toMatch(
      /body\.evidence-x-scroll\s*\{[^}]*overflow-x:\s*visible\s*!important;/,
    );
  });

  it("allows every Ant table body to chain vertical scrolling at its boundaries", () => {
    expect(globalsCss).toMatch(
      /\.ant-table-body\s*\{[^}]*overscroll-behavior-y:\s*auto;[^}]*-webkit-overflow-scrolling:\s*touch;/,
    );
  });

  it("anchors floating assistants directly above the sidebar user entry", () => {
    expect(dashboardLayout).toContain("data-dashboard-shell");
    expect(crossAnalysisLayout).toContain("data-dashboard-shell");
    expect(globalsCss).toMatch(
      /\[data-dashboard-shell\]\s*\{[^}]*--dashboard-sidebar-width:\s*260px;[^}]*--dashboard-user-entry-height:\s*4\.25rem;/,
    );
    expect(globalsCss).toMatch(
      /aside\[data-collapsed="true"\][^}]*--dashboard-sidebar-width:\s*60px;[^}]*--dashboard-user-entry-height:\s*4rem;/,
    );
    expect(globalsCss).toMatch(
      /\.dashboard-chat-launcher\s*\{[^}]*bottom:\s*calc\([^}]*var\(--dashboard-user-entry-height,\s*4\.25rem\)[^}]*\+\s*0\.75rem[^}]*\);[^}]*left:\s*1\.5rem;/,
    );
    expect(globalsCss).toMatch(
      /aside\[data-collapsed="true"\][^}]*\.dashboard-chat-launcher\s*\{[^}]*left:\s*0\.375rem;[^}]*width:\s*3rem;/,
    );
    expect(globalsCss).toMatch(
      /aside\[data-collapsed="true"\][^}]*\.dashboard-chat-launcher-label\s*\{[^}]*display:\s*none;/,
    );
  });
});
