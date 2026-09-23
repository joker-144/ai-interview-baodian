import type { Metadata } from "next";
import "./globals.css";
import { TopNav } from "@/components/TopNav";

export const metadata: Metadata = {
  title: "AI 面试宝典",
  description: "AI 面试练习平台：基于你的简历与真实在招 JD 智能出题",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <TopNav />
        <main className="mx-auto max-w-page px-6 pb-20 pt-6">{children}</main>
      </body>
    </html>
  );
}
