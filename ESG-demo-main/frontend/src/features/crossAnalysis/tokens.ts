// src/features/crossAnalysis/tokens.ts
// Visual tokens + interaction guidelines (keep consistent for "premium, warm, minimal" feel).

export const crossTokens = {
  color: {
    bg: "#FBFAF7", // warm off-white
    card: "#FFFFFF",
    cardAlt: "#F7F5F0",
    border: "rgba(20, 20, 20, 0.08)",
    text: "rgba(20, 20, 20, 0.92)",
    subtext: "rgba(20, 20, 20, 0.62)",
    accent: "#2B6CB0", // low-saturation blue
    accentSoft: "rgba(43, 108, 176, 0.10)",
  },
  radius: {
    card: 14,
    pill: 999,
  },
  shadow: {
    card: "0 8px 24px rgba(20, 20, 20, 0.06)",
    subtle: "0 2px 10px rgba(20, 20, 20, 0.05)",
  },
  spacing: {
    xs: 8,
    sm: 12,
    md: 16,
    lg: 24,
    xl: 32,
  },
  motion: {
    hoverMs: 140,
    panelMs: 220,
  },
};

// 禁用清单（强制）
// - 企业培训PPT风（大色块标题、强分隔、密集说明）
// - 土味渐变铺满背景
// - 廉价 3D 图标、霓虹高饱和
// - 重描边、强阴影、过度玻璃拟态
// - 海报式卡片堆叠、到处大按钮
