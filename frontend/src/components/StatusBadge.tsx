import { cn } from "@/lib/utils";

const statusColors: Record<string, string> = {
  UPLOADED: "bg-blue-100 text-blue-700",
  EXTRACTED: "bg-yellow-100 text-yellow-700",
  SCORED: "bg-green-100 text-green-700",
  DECIDED: "bg-purple-100 text-purple-700",
  FAILED: "bg-red-100 text-red-700",
  HIGH: "bg-green-100 text-green-700",
  MEDIUM: "bg-yellow-100 text-yellow-700",
  LOW: "bg-orange-100 text-orange-700",
  NONE: "bg-gray-100 text-gray-600",
  FOUND: "bg-green-100 text-green-700",
  INFERRED: "bg-yellow-100 text-yellow-700",
  MISSING: "bg-red-100 text-red-700",
  PASS: "bg-red-100 text-red-700",
  PURSUE: "bg-green-100 text-green-700",
};

export default function StatusBadge({ value }: { value: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium",
        statusColors[value] ?? "bg-gray-100 text-gray-600"
      )}
    >
      {value}
    </span>
  );
}
