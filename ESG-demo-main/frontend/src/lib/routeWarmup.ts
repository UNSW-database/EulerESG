type RoutePrefetcher = {
  prefetch: (href: string) => void | Promise<void>;
};

const developmentWarmups = new Map<string, Promise<void>>();

function developmentWarmupUrl(href: string): string | null {
  if (
    process.env.NODE_ENV !== "development" ||
    typeof window === "undefined"
  ) {
    return null;
  }

  try {
    const target = new URL(href, window.location.origin);
    if (target.origin !== window.location.origin || !target.pathname.startsWith("/")) {
      return null;
    }
    return `${target.pathname}${target.search}`;
  } catch {
    return null;
  }
}

/**
 * Warm an App Router destination as soon as the user shows navigation intent.
 *
 * Next's router prefetch is intentionally disabled in development. The extra
 * same-origin HTML request below is development-only and asks the Next dev
 * server to compile the route before the click completes. Production keeps the
 * normal App Router prefetch path without issuing a duplicate HTML request.
 */
export function warmAppRoute(router: RoutePrefetcher, href: string): void {
  void router.prefetch(href);

  const warmupUrl = developmentWarmupUrl(href);
  if (!warmupUrl || developmentWarmups.has(warmupUrl)) return;

  const request = fetch(warmupUrl, {
    credentials: "same-origin",
    headers: { "X-Euler-Route-Warmup": "1" },
  })
    .then(() => undefined)
    .catch(() => {
      developmentWarmups.delete(warmupUrl);
    });

  developmentWarmups.set(warmupUrl, request);
}

export function resetRouteWarmupsForTests(): void {
  developmentWarmups.clear();
}
