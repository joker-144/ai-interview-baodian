import type { Config } from "tailwindcss";

/**
 * 设计 Tokens 与原型「AI智慧面试宝典」全局一致：
 * 主蓝 #014DB2 / 暖白底 #FAFAF8 / 卡片白 + #EDEDED 边框（圆角 16px）
 * 绿=正确/完成，橙=提醒/连击，红=错误，紫=AI/模拟面试点缀
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: "#014DB2",
          light: "#E8F0FC",
          dark: "#013A87",
        },
        bg: "#FAFAF8",
        card: "#FFFFFF",
        ink: "#1F2329",
        muted: "#9AA0AB",
        line: "#EDEDED",
        success: "#1E9E6A",
        warn: "#E8833A",
        danger: "#D95555",
        ai: "#6C5CE7",
      },
      borderRadius: {
        card: "16px",
        btn: "12px",
        tag: "8px",
      },
      fontFamily: {
        sans: [
          "Noto Sans SC",
          "PingFang SC",
          "Microsoft YaHei",
          "system-ui",
          "sans-serif",
        ],
      },
      maxWidth: {
        page: "1120px",
      },
    },
  },
  plugins: [],
};

export default config;
