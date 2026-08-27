/** Development-only diagnostics. Never pass tokens, file bodies, or evidence objects. */
export const debugLog = (message: string, fields?: Record<string, unknown>) => {
  if (process.env.NODE_ENV !== "development") return;
  if (fields) console.debug(message, fields);
  else console.debug(message);
};

export const errorSummary = (error: unknown): string =>
  error instanceof Error ? error.message : String(error || "Unknown error");
