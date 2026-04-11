const API_BASE = "/api/v1";
const TENANT_ID = "00000000-0000-0000-0000-000000000001";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "X-Tenant-ID": TENANT_ID,
      "X-User-ID": TENANT_ID,
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.message || `Request failed: ${res.status}`);
  }
  return res.json();
}

// --- Types matching backend DealCardSchema ---

export interface ExtractedField {
  field_name: string;
  field_value: string | null;
  field_status: "FOUND" | "INFERRED" | "MISSING";
  confidence: "HIGH" | "MEDIUM" | "LOW" | "NONE";
}

export interface DealCard {
  id: string;
  filename: string;
  status: "UPLOADED" | "EXTRACTED" | "FAILED" | "SCORED" | "DECIDED";
  source_channel: string;
  created_at: string;

  // Extraction
  extracted_fields: ExtractedField[] | null;
  extraction_confidence: string | null;

  // Score
  score: number | null;
  score_confidence: string | null;
  rationale: string | null;

  // Decision
  decision: "PASS" | "PURSUE" | null;
  decision_notes: string | null;
  decided_at: string | null;
}

export interface Criterion {
  id?: string;
  field_name: string;
  criterion_type: "MUST_HAVE" | "NICE_TO_HAVE" | "DEALBREAKER";
  operator: string;
  target_value: unknown;
  weight: number;
  label: string;
}

export interface CriteriaConfig {
  id: string;
  tenant_id: string;
  version: number;
  is_active: boolean;
  name: string;
  criteria: Criterion[];
}

// --- API calls ---

export async function listDeals(
  limit = 50,
  offset = 0,
  status?: string
): Promise<DealCard[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (status) params.set("status", status);
  return request(`/deals?${params}`);
}

export async function getDeal(id: string): Promise<DealCard> {
  return request(`/deals/${id}`);
}

export async function uploadDeal(file: File): Promise<{ deal_id: string; status: string; message: string }> {
  const formData = new FormData();
  formData.append("file", file);
  return request("/deals/upload", {
    method: "POST",
    body: formData,
  });
}

export async function decideDeal(
  dealId: string,
  decision: "PASS" | "PURSUE",
  notes: string
): Promise<unknown> {
  return request(`/deals/${dealId}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, notes }),
  });
}

export async function getActiveConfig(): Promise<CriteriaConfig> {
  return request("/criteria/config");
}

export async function getConfigHistory(): Promise<CriteriaConfig[]> {
  return request("/criteria/config/history");
}

export async function createConfig(
  name: string,
  criteria: Omit<Criterion, "id">[]
): Promise<CriteriaConfig> {
  return request("/criteria/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, criteria }),
  });
}
