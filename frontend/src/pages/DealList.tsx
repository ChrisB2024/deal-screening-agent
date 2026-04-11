import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listDeals, type DealCard } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import { FileText } from "lucide-react";

const STATUSES = ["ALL", "UPLOADED", "EXTRACTED", "SCORED", "DECIDED", "FAILED"];

export default function DealList() {
  const [deals, setDeals] = useState<DealCard[]>([]);
  const [status, setStatus] = useState("ALL");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function fetchDeals() {
      setLoading(true);
      try {
        const res = await listDeals(50, 0, status === "ALL" ? undefined : status);
        if (!cancelled) setDeals(res);
      } catch (e) {
        if (!cancelled) console.error(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    fetchDeals();
    return () => { cancelled = true; };
  }, [status]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold">Deal Pipeline</h1>
        <div className="flex gap-1">
          {STATUSES.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                status === s
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="text-center py-20 text-muted-foreground">Loading deals...</div>
      ) : deals.length === 0 ? (
        <div className="text-center py-20">
          <FileText className="mx-auto h-12 w-12 text-muted-foreground/50 mb-3" />
          <p className="text-muted-foreground">No deals found.</p>
          <Link
            to="/upload"
            className="inline-block mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium"
          >
            Upload a Deal
          </Link>
        </div>
      ) : (
        <div className="grid gap-3">
          {deals.map((deal) => (
            <Link
              key={deal.id}
              to={`/deals/${deal.id}`}
              className="flex items-center justify-between p-4 bg-card border rounded-lg hover:shadow transition-shadow"
            >
              <div className="flex items-center gap-4">
                <FileText className="h-5 w-5 text-muted-foreground" />
                <div>
                  <p className="font-medium">{deal.filename}</p>
                  <p className="text-sm text-muted-foreground">
                    {new Date(deal.created_at).toLocaleDateString()}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-4">
                {deal.score !== null && (
                  <div className="text-right">
                    <p className="text-lg font-semibold">{deal.score}%</p>
                    {deal.score_confidence && (
                      <StatusBadge value={deal.score_confidence} />
                    )}
                  </div>
                )}
                <StatusBadge value={deal.status} />
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
