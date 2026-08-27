"use client";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { LogOut, Settings, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import EulerLogo from "@/assets/Euler-Img.svg";
import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import { clearAuth, getStoredAuth } from "@/lib/auth";
import { useRouter } from "next/navigation";
import { useFileStore } from "@/store/useFileStore";
import { useAppLang } from "@/i18n/useAppLang";
import { useT } from "@/i18n/useT";

export default function Nav({ className }: { className?: string }) {
  const router = useRouter();
  const clearFiles = useFileStore((s) => s.clearFiles);
  const [displayName, setDisplayName] = useState<string>("User");

  const { lang, setLang } = useAppLang();
  const { t } = useT();

  // ⚠️ 避免在首屏渲染阶段读取 localStorage（SSR/CSR 初始值不一致会触发 hydration mismatch）
  // displayName 初始为 "User"，并在 useEffect 中再从 localStorage 更新
  const initials = useMemo(() => {
    const ch = (displayName || "User").trim().slice(0, 1);
    return (ch || "U").toUpperCase();
  }, [displayName]);

  useEffect(() => {
    const auth = getStoredAuth();
    if (auth?.name || auth?.email) {
      setDisplayName(auth.name || auth.email || "User");
    }
  }, []);

  const handleLogout = () => {
    clearAuth();
    clearFiles(); // 清空前一个用户的文件列表，避免账号切换时残留
    router.push("/login");
  };

  return (
    <nav
      className={`shadow-md border-b border-gray-300 px-6 py-3 flex items-center justify-between w-full bg-white ${className}`}>
      <button
        type="button"
        onClick={() => router.push("/dashboard")}
        className="flex items-center gap-4 cursor-pointer bg-transparent border-0 p-0"
        aria-label={t("nav.goToAllFiles")}
      >
        <Image src={EulerLogo} alt="Euler Logo" className="w-8 sm:w-10 h-auto" />
        <h1 className="!text-base sm:!text-xl font-semibold text-[#2274BC]">
          EulerESG
        </h1>
      </button>

      <div className="flex items-center gap-3">
        <div className="hidden sm:flex items-center space-x-2">
          <span className="text-[#2274BC]">{t("nav.welcome")}</span>
          <span className="font-medium text-primary truncate max-w-[200px]">
            {displayName}
          </span>
        </div>

        <div className="hidden md:flex items-center rounded-full px-1 h-10 border border-black/10 bg-white/70 hover:bg-white shadow-[0_2px_10px_rgba(0,0,0,0.05)]">
          <button
            type="button"
            onClick={() => setLang("zh")}
            className={`px-3 h-8 rounded-full text-sm font-medium transition-colors ${
              lang === "zh"
                ? "bg-[#2274BC] text-white"
                : "text-[#0F172A] hover:bg-black/5"
            }`}
            aria-pressed={lang === "zh"}
          >
            中文
          </button>
          <button
            type="button"
            onClick={() => setLang("en")}
            className={`px-3 h-8 rounded-full text-sm font-medium transition-colors ${
              lang === "en"
                ? "bg-[#2274BC] text-white"
                : "text-[#0F172A] hover:bg-black/5"
            }`}
            aria-pressed={lang === "en"}
          >
            EN
          </button>
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="rounded-full h-10 w-10 sm:h-12 sm:w-12 p-0 cursor-pointer"
              aria-label={t("nav.userMenu")}
            >
              <Avatar className="h-8 w-8">
                <AvatarFallback className="text-sm sm:text-base">
                  {initials}
                </AvatarFallback>
              </Avatar>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-[150px] sm:min-w-[180px]">
            <DropdownMenuLabel className="text-sm sm:text-base">
              {t("nav.myAccount")}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-sm sm:text-base">
              <User className="mr-2 h-4 w-4" />
              <span>{t("nav.profile")}</span>
            </DropdownMenuItem>
            <DropdownMenuItem className="text-sm sm:text-base">
              <Settings className="mr-2 h-4 w-4" />
              <span>{t("nav.settings")}</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-sm sm:text-base" onClick={handleLogout}>
              <LogOut className="mr-2 h-4 w-4" />
              <span>{t("nav.logout")}</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  );
}
