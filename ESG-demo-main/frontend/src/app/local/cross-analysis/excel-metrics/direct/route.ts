import { NextResponse } from "next/server";
import fs from "node:fs/promises";
import path from "node:path";

// Local route (NOT proxied to backend) to read persisted Cross Analysis output JSON
// from the filesystem visible to the frontend runtime.
//
// For docker-compose, mount the host ./uploads directory into the frontend container
// (recommended target: /app/uploads) OR set CROSS_ALL_RECORDS_PATH.

const CANDIDATE_RELATIVE_PATHS = [
  "uploads/outputs/cross_analysis/output/all_records.json",
  "uploads/outputs/cross_analysis/excel_output/all_records.json",
];

async function fileExists(p: string): Promise<boolean> {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

async function resolveAllRecordsPath(): Promise<string | null> {
  const explicit = process.env.CROSS_ALL_RECORDS_PATH;
  if (explicit && (await fileExists(explicit))) return explicit;

  // Try relative to project root (process.cwd())
  for (const rel of CANDIDATE_RELATIVE_PATHS) {
    const p = path.join(process.cwd(), rel);
    if (await fileExists(p)) return p;
  }

  // Try absolute /uploads (some deployments mount uploads at container root)
  for (const rel of CANDIDATE_RELATIVE_PATHS) {
    const p = path.join("/", rel);
    if (await fileExists(p)) return p;
  }

  return null;
}

export async function GET() {
  const p = await resolveAllRecordsPath();
  if (!p) {
    return NextResponse.json(
      {
        error:
          "all_records.json not found. Mount ./uploads into the frontend container (e.g., to /app/uploads) or set CROSS_ALL_RECORDS_PATH.",
      },
      { status: 404 }
    );
  }

  const text = await fs.readFile(p, "utf-8");
  const parsed = JSON.parse(text);
  const records = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.records) ? parsed.records : [];

  return NextResponse.json({
    records,
    generated_at: new Date().toISOString(),
    source: p,
  });
}
