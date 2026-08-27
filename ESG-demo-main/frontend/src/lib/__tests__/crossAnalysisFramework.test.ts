import { beforeEach, describe, expect, it } from "vitest";

import {
  applyFrameworkToSearchParams,
  CROSS_FRAMEWORK_LS_KEY,
  readCachedCrossFrameworkSelection,
  writeCachedCrossFrameworkSelection,
} from "../crossAnalysisFramework";

describe("cross-analysis framework selection", () => {
  beforeEach(() => window.localStorage.clear());

  it("discards a retired TCFD selection from browser storage", () => {
    window.localStorage.setItem(
      CROSS_FRAMEWORK_LS_KEY,
      JSON.stringify({ framework: "TCFD", industry: "TCFD", semiIndustry: "governance" }),
    );

    expect(readCachedCrossFrameworkSelection()).toEqual({});
    expect(window.localStorage.getItem(CROSS_FRAMEWORK_LS_KEY)).toBeNull();
  });

  it("does not write or serialize TCFD into a new comparison URL", () => {
    writeCachedCrossFrameworkSelection({ framework: "TCFD", semiIndustry: "governance" });
    expect(window.localStorage.getItem(CROSS_FRAMEWORK_LS_KEY)).toBeNull();

    const result = applyFrameworkToSearchParams(
      new URLSearchParams("framework=TCFD&industry=TCFD&semiIndustry=governance&ids=a,b"),
      { framework: "TCFD", semiIndustry: "governance" },
    );
    expect(result.toString()).toBe("ids=a%2Cb");
  });

  it("continues to normalize supported framework selections", () => {
    const result = applyFrameworkToSearchParams(new URLSearchParams(), {
      framework: " cdp ",
      semiIndustry: "climate",
    });
    expect(result.get("framework")).toBe("CDP");
    expect(result.get("industry")).toBe("CDP");
    expect(result.get("semiIndustry")).toBe("climate");
  });
});
