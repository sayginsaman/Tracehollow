import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/** Liveness of the web server process only; it does not contact the API. */
export function GET() {
  return NextResponse.json({ status: "ok" }, { headers: { "cache-control": "no-store" } });
}
