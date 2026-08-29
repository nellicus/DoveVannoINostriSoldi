import { EurostatTaxagContractError, getEurostatTaxagView } from "@/lib/eurostat-taxag";

const CACHE_CONTROL = "public, max-age=3600, stale-while-revalidate=86400";

export function createEurostatTaxagResponse(loadView: typeof getEurostatTaxagView = getEurostatTaxagView) {
  try {
    return Response.json(loadView(), { headers: { "Cache-Control": CACHE_CONTROL } });
  } catch (error) {
    if (error instanceof EurostatTaxagContractError) {
      return Response.json(
        { ok: false, error: "snapshot_contract_invalid" },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }
    throw error;
  }
}

export async function GET() {
  return createEurostatTaxagResponse();
}
