"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { AUTH_TOKEN_KEY } from "@/lib/auth";
import { warmAppRoute } from "@/lib/routeWarmup";

export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem(AUTH_TOKEN_KEY) : null;
    const target = token ? "/dashboard" : "/login";
    warmAppRoute(router, target);
    router.replace(target);
  }, [router]);

  return (
    <main
      aria-busy="true"
      aria-label="Loading application"
      className="grid min-h-screen place-items-center bg-slate-50"
    >
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-[#2274BC]" />
    </main>
  );
}
