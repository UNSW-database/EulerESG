import { describe, expect, it } from "vitest";

import {
  ACTIVE_FRAMEWORK_OPTIONS,
  isActiveFramework,
  UPLOAD_FRAMEWORK_OPTIONS,
} from "../frameworkOptions";

describe("active framework options", () => {
  it("offers only the supported frameworks for new work", () => {
    expect(ACTIVE_FRAMEWORK_OPTIONS.map((option) => option.value)).toEqual([
      "SASB",
      "GRI",
      "CDP",
    ]);
    expect(isActiveFramework("TCFD")).toBe(false);
    expect(isActiveFramework(" cdp ")).toBe(true);
  });

  it("adds enabled AASB only to the report-upload choices", () => {
    expect(UPLOAD_FRAMEWORK_OPTIONS).toEqual([
      { label: "SASB", value: "SASB" },
      { label: "GRI", value: "GRI" },
      { label: "CDP", value: "CDP" },
      { label: "AASB", value: "AASB" },
    ]);
    expect(UPLOAD_FRAMEWORK_OPTIONS.find(({ value }) => value === "AASB")).not.toHaveProperty(
      "disabled",
      true,
    );
    expect(ACTIVE_FRAMEWORK_OPTIONS.map(({ value }) => value)).not.toContain("AASB");
    expect(isActiveFramework("AASB")).toBe(false);
  });
});
