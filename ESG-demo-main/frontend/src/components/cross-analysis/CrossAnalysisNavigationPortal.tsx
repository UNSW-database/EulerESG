"use client";

import { createContext, useContext } from "react";

export const CrossAnalysisNavigationSlotContext = createContext<HTMLElement | null>(null);

export function useCrossAnalysisNavigationSlot(): HTMLElement | null {
  return useContext(CrossAnalysisNavigationSlotContext);
}
