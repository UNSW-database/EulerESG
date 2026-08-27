/** Frameworks currently backed by disclosure-analysis metric data. */
export const ACTIVE_FRAMEWORK_OPTIONS = [
  { label: "SASB", value: "SASB" },
  { label: "GRI", value: "GRI" },
  { label: "CDP", value: "CDP" },
];

/** Framework choices shown by the report-upload form. */
export const UPLOAD_FRAMEWORK_OPTIONS = [
  ...ACTIVE_FRAMEWORK_OPTIONS,
  { label: "AASB", value: "AASB" },
];

export function isActiveFramework(value: unknown): boolean {
  const normalized = String(value ?? "").trim().toUpperCase();
  return ACTIVE_FRAMEWORK_OPTIONS.some((option) => option.value === normalized);
}
