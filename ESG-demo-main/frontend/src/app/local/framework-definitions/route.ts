import { NextRequest, NextResponse } from "next/server";
import fs from "node:fs/promises";
import type { Dirent } from "node:fs";
import path from "node:path";

export const runtime = "nodejs";

const MAX_DEPTH = 5;
const MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024;
const IGNORED_DIRS = new Set(["node_modules", ".git", ".next", "dist", "build", "coverage"]);
const SASB_ROOT_CANDIDATES = [
  process.env.SASB_METRICS_PATH,
  path.join(process.cwd(), "backend", "data", "sasb_metrics"),
  path.join(process.cwd(), "..", "backend", "data", "sasb_metrics"),
  path.join(process.cwd(), "..", "..", "backend", "data", "sasb_metrics"),
].filter(Boolean) as string[];
const MANIFEST_CANDIDATES = [
  path.join(process.cwd(), "src", "data", "sasb_metrics_manifest.json"),
  path.join(process.cwd(), "data", "sasb_metrics_manifest.json"),
  ...SASB_ROOT_CANDIDATES.map((root) => path.join(root, "manifest.json")),
];

type DefinitionMap = Record<string, string>;
type ManifestShape = { semi_industry_to_file?: Record<string, string> };

type MatchQuery = {
  framework: string;
  industry: string;
  semiIndustry: string;
  disclosureFramework: string;
};

type RouteResult = {
  definitions: DefinitionMap;
  matchedFile: string | null;
  sourcePath: string | null;
  matchedFramework: string | null;
};

const routeCache = new Map<string, RouteResult>();
const fileListCache = new Map<string, string[]>();
let manifestCache: Record<string, string> | null = null;

const normalizeText = (value: unknown): string =>
  String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[–—]/g, "-")
    .replace(/[·•]/g, " ")
    .replace(/&/g, " and ")
    .replace(/[_/]+/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

function buildQuery(searchParams: URLSearchParams): MatchQuery {
  const semiIndustry = String(
    searchParams.get("semiIndustry") ?? searchParams.get("semi_industry") ?? ""
  ).trim();
  const disclosureFramework = String(
    searchParams.get("disclosureFramework") ?? searchParams.get("disclosure_framework") ?? semiIndustry
  ).trim();

  return {
    framework: normalizeText(searchParams.get("framework") ?? ""),
    industry: String(searchParams.get("industry") ?? "").trim(),
    semiIndustry,
    disclosureFramework,
  };
}

async function exists(p: string): Promise<boolean> {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

async function walkJsonFiles(dir: string, depth = 0): Promise<string[]> {
  const cacheKey = `${dir}::${depth}`;
  const cached = fileListCache.get(cacheKey);
  if (cached) return cached;

  if (depth > MAX_DEPTH || !(await exists(dir))) {
    fileListCache.set(cacheKey, []);
    return [];
  }

  let entries: Dirent<string>[] = [];
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    fileListCache.set(cacheKey, []);
    return [];
  }

  const out: string[] = [];
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (IGNORED_DIRS.has(entry.name)) continue;
      out.push(...(await walkJsonFiles(fullPath, depth + 1)));
      continue;
    }
    if (entry.isFile() && entry.name.toLowerCase().endsWith(".json")) {
      out.push(fullPath);
    }
  }

  fileListCache.set(cacheKey, out);
  return out;
}

async function loadManifestMapping(): Promise<Record<string, string>> {
  if (manifestCache) return manifestCache;

  for (const manifestPath of MANIFEST_CANDIDATES) {
    try {
      if (!(await exists(manifestPath))) continue;
      const raw = await fs.readFile(manifestPath, "utf-8");
      const parsed = JSON.parse(raw) as ManifestShape;
      const mapping = parsed?.semi_industry_to_file;
      if (mapping && typeof mapping === "object") {
        manifestCache = mapping;
        return mapping;
      }
    } catch {
      continue;
    }
  }

  manifestCache = {};
  return manifestCache;
}

function addDefinition(map: DefinitionMap, key: unknown, definition: unknown) {
  const normalizedKey = normalizeText(key);
  const text = typeof definition === "string" ? definition.trim() : "";
  if (!normalizedKey || !text) return;
  if (!map[normalizedKey]) map[normalizedKey] = text;
}

function extractDefinitionsFromJson(input: unknown, output: DefinitionMap, seen = new WeakSet<object>()) {
  if (input === null || input === undefined) return;

  if (Array.isArray(input)) {
    for (const item of input) extractDefinitionsFromJson(item, output, seen);
    return;
  }

  if (typeof input !== "object") return;
  if (seen.has(input as object)) return;
  seen.add(input as object);

  const obj = input as Record<string, unknown>;
  const definition = obj.definition ?? obj.Definition ?? obj.metric_definition ?? obj.metricDefinition;

  if (typeof definition === "string" && definition.trim()) {
    const metricId = obj.metric_id ?? obj.metric_code ?? obj.metricId ?? obj.Code ?? obj.code;
    const metricName = obj.metric_name ?? obj.Metric ?? obj.metric ?? obj.name ?? obj.Name;

    addDefinition(output, metricId, definition);
    addDefinition(output, metricName, definition);

    const metricIdText = String(metricId ?? "").trim();
    if (metricIdText.includes("(")) {
      addDefinition(output, metricIdText.replace(/\([^)]*\)/g, " "), definition);
    }
  }

  for (const value of Object.values(obj)) {
    if (value && typeof value === "object") {
      extractDefinitionsFromJson(value, output, seen);
    }
  }
}

function getCandidateFrameworkNames(query: MatchQuery): string[] {
  const values = [query.disclosureFramework, query.semiIndustry]
    .map((value) => String(value ?? "").trim())
    .filter(Boolean);
  return Array.from(new Set(values));
}

function resolveManifestFilename(mapping: Record<string, string>, query: MatchQuery): { framework: string; filename: string } | null {
  const candidates = getCandidateFrameworkNames(query);
  if (!candidates.length) return null;

  for (const frameworkName of candidates) {
    if (mapping[frameworkName]) {
      return { framework: frameworkName, filename: mapping[frameworkName] };
    }
  }

  const normalizedEntries = Object.entries(mapping).map(([frameworkName, filename]) => ({
    frameworkName,
    normalizedFrameworkName: normalizeText(frameworkName),
    filename,
  }));

  for (const frameworkName of candidates) {
    const normalizedFrameworkName = normalizeText(frameworkName);
    const hit = normalizedEntries.find((entry) => entry.normalizedFrameworkName === normalizedFrameworkName);
    if (hit) {
      return { framework: hit.frameworkName, filename: hit.filename };
    }
  }

  return null;
}

function buildDirectManifestPaths(filename: string): string[] {
  const cleanName = path.basename(String(filename ?? "").trim());
  if (!cleanName) return [];

  return Array.from(
    new Set(
      SASB_ROOT_CANDIDATES.map((root) => path.join(root, cleanName))
    )
  );
}

async function resolveMatchedFileFromManifest(filename: string): Promise<string | null> {
  for (const candidatePath of buildDirectManifestPaths(filename)) {
    if (await exists(candidatePath)) {
      return candidatePath;
    }
  }
  return null;
}

function getBaseName(filePath: string): string {
  return path.basename(filePath, path.extname(filePath));
}

function resolveMatchedFileByFilename(files: string[], filename: string): string | null {
  const targetName = path.basename(filename).toLowerCase();
  const exact = files.find((filePath) => path.basename(filePath).toLowerCase() === targetName);
  if (exact) return exact;

  const targetBase = getBaseName(filename).toLowerCase();
  const baseHit = files.find((filePath) => getBaseName(filePath).toLowerCase() === targetBase);
  return baseHit ?? null;
}

async function loadDefinitions(searchParams: URLSearchParams): Promise<RouteResult> {
  const cacheKey = searchParams.toString();
  const cached = routeCache.get(cacheKey);
  if (cached) return cached;

  const query = buildQuery(searchParams);
  if (query.framework !== "sasb") {
    const result: RouteResult = { definitions: {}, matchedFile: null, sourcePath: null, matchedFramework: null };
    routeCache.set(cacheKey, result);
    return result;
  }

  const manifestMapping = await loadManifestMapping();
  const manifestHit = resolveManifestFilename(manifestMapping, query);

  let matchedFilePath = manifestHit ? await resolveMatchedFileFromManifest(manifestHit.filename) : null;

  if (!matchedFilePath) {
    const roots = Array.from(new Set(SASB_ROOT_CANDIDATES));
    const jsonFiles = (await Promise.all(roots.map((root) => walkJsonFiles(root)))).flat();
    if (!jsonFiles.length) {
      const result: RouteResult = { definitions: {}, matchedFile: null, sourcePath: null, matchedFramework: null };
      routeCache.set(cacheKey, result);
      return result;
    }
    matchedFilePath = manifestHit ? resolveMatchedFileByFilename(jsonFiles, manifestHit.filename) : null;
  }

  if (!matchedFilePath) {
    const result: RouteResult = {
      definitions: {},
      matchedFile: manifestHit?.filename ?? null,
      sourcePath: null,
      matchedFramework: manifestHit?.framework ?? getCandidateFrameworkNames(query)[0] ?? null,
    };
    routeCache.set(cacheKey, result);
    return result;
  }

  const definitions: DefinitionMap = {};
  try {
    const stat = await fs.stat(matchedFilePath);
    if (stat.size <= MAX_FILE_SIZE_BYTES) {
      const raw = await fs.readFile(matchedFilePath, "utf-8");
      const parsed = JSON.parse(raw);
      extractDefinitionsFromJson(parsed, definitions);
    }
  } catch {
    const result: RouteResult = {
      definitions: {},
      matchedFile: path.basename(matchedFilePath),
      sourcePath: matchedFilePath,
      matchedFramework: manifestHit?.framework ?? getCandidateFrameworkNames(query)[0] ?? null,
    };
    routeCache.set(cacheKey, result);
    return result;
  }

  const result: RouteResult = {
    definitions,
    matchedFile: path.basename(matchedFilePath),
    sourcePath: matchedFilePath,
    matchedFramework: manifestHit?.framework ?? getCandidateFrameworkNames(query)[0] ?? null,
  };
  routeCache.set(cacheKey, result);
  return result;
}

export async function GET(request: NextRequest) {
  const result = await loadDefinitions(request.nextUrl.searchParams);
  return NextResponse.json({
    ...result,
    count: Object.keys(result.definitions).length,
    source: "backend/data/sasb_metrics",
  });
}
