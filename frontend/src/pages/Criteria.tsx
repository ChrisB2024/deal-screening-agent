import { useEffect, useState } from "react";
import {
  getActiveConfig,
  getConfigHistory,
  createConfig,
  type CriteriaConfig,
  type Criterion,
} from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import { Plus, Trash2, Save } from "lucide-react";

const EMPTY_CRITERION: Omit<Criterion, "id"> = {
  field_name: "sector",
  criterion_type: "MUST_HAVE",
  operator: "eq",
  target_value: "",
  weight: 1.0,
  label: "",
};

const FIELDS = ["sector", "revenue", "ebitda", "geography", "ask_price", "deal_type"];
const TYPES = ["MUST_HAVE", "NICE_TO_HAVE", "DEALBREAKER"] as const;
const OPERATORS = ["eq", "ne", "gt", "lt", "gte", "lte", "in", "not_in", "contains"];

export default function Criteria() {
  const [active, setActive] = useState<CriteriaConfig | null>(null);
  const [history, setHistory] = useState<CriteriaConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // New config form
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [criteria, setCriteria] = useState<Omit<Criterion, "id">[]>([{ ...EMPTY_CRITERION }]);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [activeRes, historyRes] = await Promise.allSettled([
        getActiveConfig(),
        getConfigHistory(),
      ]);
      if (activeRes.status === "fulfilled") setActive(activeRes.value);
      else setActive(null);
      if (historyRes.status === "fulfilled") setHistory(historyRes.value);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function addCriterion() {
    setCriteria([...criteria, { ...EMPTY_CRITERION }]);
  }

  function removeCriterion(idx: number) {
    setCriteria(criteria.filter((_, i) => i !== idx));
  }

  function updateCriterion(idx: number, updates: Partial<Omit<Criterion, "id">>) {
    setCriteria(criteria.map((c, i) => (i === idx ? { ...c, ...updates } : c)));
  }

  async function handleSave() {
    if (!name.trim() || criteria.length === 0) return;
    setSaving(true);
    setError("");
    try {
      // Parse target_value as JSON if possible
      const parsed = criteria.map((c) => ({
        ...c,
        target_value:
          typeof c.target_value === "string"
            ? tryParseJSON(c.target_value as string)
            : c.target_value,
      }));
      await createConfig(name, parsed);
      setEditing(false);
      setName("");
      setCriteria([{ ...EMPTY_CRITERION }]);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="py-20 text-center text-muted-foreground">Loading...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold">Screening Criteria</h1>
        {!editing && (
          <button
            onClick={() => setEditing(true)}
            className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium"
          >
            <Plus className="h-4 w-4" /> New Config
          </button>
        )}
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-md text-sm">{error}</div>
      )}

      {/* New Config Form */}
      {editing && (
        <div className="border rounded-lg p-5 mb-6 bg-card">
          <h2 className="text-lg font-semibold mb-4">New Criteria Config</h2>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Config name (e.g. Q1 2026 Screening)"
            className="w-full border rounded-md p-2.5 text-sm mb-4 bg-background"
          />

          {criteria.map((c, idx) => (
            <div key={idx} className="flex flex-wrap gap-2 mb-3 p-3 bg-muted/50 rounded-md items-end">
              <div className="flex-1 min-w-[120px]">
                <label className="text-xs text-muted-foreground">Field</label>
                <select
                  value={c.field_name}
                  onChange={(e) => updateCriterion(idx, { field_name: e.target.value })}
                  className="w-full border rounded-md p-2 text-sm bg-background"
                >
                  {FIELDS.map((f) => (
                    <option key={f} value={f}>{f}</option>
                  ))}
                </select>
              </div>
              <div className="min-w-[130px]">
                <label className="text-xs text-muted-foreground">Type</label>
                <select
                  value={c.criterion_type}
                  onChange={(e) =>
                    updateCriterion(idx, { criterion_type: e.target.value as typeof TYPES[number] })
                  }
                  className="w-full border rounded-md p-2 text-sm bg-background"
                >
                  {TYPES.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <div className="min-w-[90px]">
                <label className="text-xs text-muted-foreground">Operator</label>
                <select
                  value={c.operator}
                  onChange={(e) => updateCriterion(idx, { operator: e.target.value })}
                  className="w-full border rounded-md p-2 text-sm bg-background"
                >
                  {OPERATORS.map((o) => (
                    <option key={o} value={o}>{o}</option>
                  ))}
                </select>
              </div>
              <div className="flex-1 min-w-[120px]">
                <label className="text-xs text-muted-foreground">Target Value</label>
                <input
                  value={typeof c.target_value === "string" ? c.target_value : JSON.stringify(c.target_value)}
                  onChange={(e) => updateCriterion(idx, { target_value: e.target.value })}
                  placeholder='e.g. "SaaS" or 1000000'
                  className="w-full border rounded-md p-2 text-sm bg-background"
                />
              </div>
              <div className="w-[70px]">
                <label className="text-xs text-muted-foreground">Weight</label>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  max="10"
                  value={c.weight}
                  onChange={(e) => updateCriterion(idx, { weight: parseFloat(e.target.value) || 0 })}
                  className="w-full border rounded-md p-2 text-sm bg-background"
                />
              </div>
              <div className="flex-1 min-w-[120px]">
                <label className="text-xs text-muted-foreground">Label</label>
                <input
                  value={c.label}
                  onChange={(e) => updateCriterion(idx, { label: e.target.value })}
                  placeholder="Human-readable label"
                  className="w-full border rounded-md p-2 text-sm bg-background"
                />
              </div>
              <button
                onClick={() => removeCriterion(idx)}
                className="p-2 text-red-500 hover:bg-red-50 rounded-md"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}

          <div className="flex gap-3 mt-4">
            <button
              onClick={addCriterion}
              className="flex items-center gap-1 px-3 py-2 border rounded-md text-sm hover:bg-accent"
            >
              <Plus className="h-4 w-4" /> Add Criterion
            </button>
            <div className="flex-1" />
            <button
              onClick={() => setEditing(false)}
              className="px-4 py-2 border rounded-md text-sm hover:bg-accent"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving || !name.trim() || criteria.length === 0}
              className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium disabled:opacity-50"
            >
              <Save className="h-4 w-4" /> {saving ? "Saving..." : "Save Config"}
            </button>
          </div>
        </div>
      )}

      {/* Active Config */}
      {active && (
        <div className="border rounded-lg p-5 mb-6 bg-card">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">{active.name}</h2>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">v{active.version}</span>
              <StatusBadge value={active.is_active ? "ACTIVE" : "INACTIVE"} />
            </div>
          </div>
          <div className="space-y-2">
            {active.criteria.map((c) => (
              <div
                key={c.id}
                className="flex items-center justify-between p-3 bg-muted/50 rounded-md text-sm"
              >
                <div className="flex items-center gap-3">
                  <StatusBadge value={c.criterion_type} />
                  <span className="font-medium">{c.label || c.field_name}</span>
                </div>
                <span className="text-muted-foreground">
                  {c.field_name} {c.operator} {JSON.stringify(c.target_value)} (w: {c.weight})
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* History */}
      {history.length > 1 && (
        <div>
          <h2 className="text-lg font-semibold mb-3">Version History</h2>
          <div className="space-y-2">
            {history
              .filter((c) => !c.is_active)
              .map((c) => (
                <div key={c.id} className="flex items-center justify-between p-3 border rounded-md text-sm">
                  <span>
                    v{c.version} — {c.name}
                  </span>
                  <span className="text-muted-foreground">
                    {c.criteria.length} criteria
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}

function tryParseJSON(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
