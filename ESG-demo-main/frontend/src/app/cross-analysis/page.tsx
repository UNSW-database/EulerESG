"use client";

// Keep the neutral entry route mounted as the comparison page itself. This
// avoids an intermediate client redirect before the selected reports load.
export { default } from "./[dimension]/page";
