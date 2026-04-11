import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getDeal, decideDeal, type DealCard } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import { ArrowLeft } from "lucide-react";

export default function DealDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [deal, setDeal] = useState<DealCard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Decision form
  const [decision, setDecision] = useState<"PASS" | "PURSUE">("PURSUE");
  const [notes, setNotes] = useState("");
  const [deciding, setDeciding] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    async function fetchDeal() {
      try {
        const data = await getDeal(id!);
        if (!cancelled) setDeal(data);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load deal");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    fetchDeal();
    return () => { cancelled = true; };
  }, [id]);

  async function handleDecide() {
    if (!id || !notes.trim()) return;
    setDeciding(true);
    try {
      await decideDeal(id, decision, notes);
      const updated = await getDeal(id);
      setDeal(updated);
      setNotes("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Decision failed");
    } finally {
      setDeciding(false);
    }
  }

  if (loading) return <div className="py-20 text-center text-muted-foreground">Loading...</div>;
  if (error) return <div className="py-20 text-center text-destructive">{error}</div>;
  if (!deal) return null;

  return (
    <div>
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-4"
      >
        <ArrowLeft className="h-4 w-4" /> Back
      </button>

      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">{deal.filename}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Uploaded {new Date(deal.created_at).toLocaleString()} &middot; {deal.source_channel}
          </p>
        </div>
        <StatusBadge value={deal.status} />
      </div>

      {/* Score Summary */}
      {deal.score !== null && (
        <div className="border rounded-lg p-5 mb-6 bg-card">
          <h2 className="text-lg font-semibold mb-3">Score</h2>
          <div className="flex items-center gap-6 mb-3">
            <div>
              <p className="text-3xl font-bold">{deal.score}%</p>
              {deal.score_confidence && <StatusBadge value={deal.score_confidence} />}
            </div>
            <div className="flex-1">
              <div className="w-full bg-muted rounded-full h-3">
                <div
                  className="bg-primary h-3 rounded-full transition-all"
                  style={{ width: `${Math.min(100, deal.score)}%` }}
                />
              </div>
            </div>
          </div>
          {deal.rationale && (
            <p className="text-sm text-muted-foreground">{deal.rationale}</p>
          )}
        </div>
      )}

      {/* Extracted Fields */}
      {deal.extracted_fields && deal.extracted_fields.length > 0 && (
        <div className="border rounded-lg p-5 mb-6 bg-card">
          <h2 className="text-lg font-semibold mb-3">Extracted Fields</h2>
          {deal.extraction_confidence && (
            <div className="mb-3 flex items-center gap-2 text-sm text-muted-foreground">
              Overall confidence: <StatusBadge value={deal.extraction_confidence} />
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {deal.extracted_fields.map((f) => (
              <div key={f.field_name} className="flex items-start justify-between p-3 bg-muted/50 rounded-md">
                <div>
                  <p className="text-sm font-medium capitalize">
                    {f.field_name.replace(/_/g, " ")}
                  </p>
                  <p className="text-sm mt-0.5">
                    {f.field_value ?? "—"}
                  </p>
                </div>
                <div className="flex gap-1.5">
                  <StatusBadge value={f.field_status} />
                  <StatusBadge value={f.confidence} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Decision */}
      {deal.decision && (
        <div className="border rounded-lg p-5 mb-6 bg-card">
          <h2 className="text-lg font-semibold mb-3">Decision</h2>
          <div className="flex items-center gap-3 mb-2">
            <StatusBadge value={deal.decision} />
            {deal.decided_at && (
              <span className="text-sm text-muted-foreground">
                {new Date(deal.decided_at).toLocaleString()}
              </span>
            )}
          </div>
          {deal.decision_notes && <p className="text-sm">{deal.decision_notes}</p>}
        </div>
      )}

      {/* Decision Form — only for SCORED deals */}
      {deal.status === "SCORED" && (
        <div className="border rounded-lg p-5 bg-card">
          <h2 className="text-lg font-semibold mb-3">Make Decision</h2>
          <div className="flex gap-3 mb-3">
            {(["PURSUE", "PASS"] as const).map((d) => (
              <button
                key={d}
                onClick={() => setDecision(d)}
                className={`px-4 py-2 rounded-md text-sm font-medium border transition-colors ${
                  decision === d
                    ? d === "PURSUE"
                      ? "bg-green-600 text-white border-green-600"
                      : "bg-red-600 text-white border-red-600"
                    : "border-border text-muted-foreground hover:bg-accent"
                }`}
              >
                {d}
              </button>
            ))}
          </div>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes for your decision..."
            className="w-full border rounded-md p-3 text-sm min-h-[80px] mb-3 bg-background"
          />
          <button
            onClick={handleDecide}
            disabled={deciding || !notes.trim()}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium disabled:opacity-50"
          >
            {deciding ? "Submitting..." : "Submit Decision"}
          </button>
        </div>
      )}
    </div>
  );
}
