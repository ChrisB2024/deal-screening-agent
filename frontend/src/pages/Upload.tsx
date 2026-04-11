import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { uploadDeal } from "@/lib/api";
import { Upload as UploadIcon, FileText, CheckCircle, AlertCircle } from "lucide-react";

export default function Upload() {
  const navigate = useNavigate();
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const handleFile = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setError("Only PDF files are accepted.");
        return;
      }
      setError("");
      setSuccess("");
      setUploading(true);
      try {
        const result = await uploadDeal(file);
        setSuccess(`Deal uploaded successfully! Status: ${result.status}`);
        setTimeout(() => navigate(`/deals/${result.deal_id}`), 1500);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [navigate]
  );

  return (
    <div className="max-w-xl mx-auto">
      <h1 className="text-2xl font-semibold mb-6">Upload Deal</h1>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const file = e.dataTransfer.files[0];
          if (file) handleFile(file);
        }}
        className={`border-2 border-dashed rounded-lg p-12 text-center transition-colors ${
          dragOver ? "border-primary bg-primary/5" : "border-border"
        }`}
      >
        {uploading ? (
          <div className="flex flex-col items-center gap-3">
            <div className="h-10 w-10 border-4 border-primary border-t-transparent rounded-full animate-spin" />
            <p className="text-sm text-muted-foreground">Processing deal...</p>
          </div>
        ) : (
          <>
            <UploadIcon className="mx-auto h-10 w-10 text-muted-foreground/50 mb-3" />
            <p className="font-medium mb-1">Drop a PDF here</p>
            <p className="text-sm text-muted-foreground mb-4">or click to browse</p>
            <label className="inline-block px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium cursor-pointer hover:opacity-90">
              Choose File
              <input
                type="file"
                accept=".pdf"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) handleFile(file);
                }}
              />
            </label>
          </>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 mt-4 p-3 bg-red-50 text-red-700 rounded-md text-sm">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {success && (
        <div className="flex items-center gap-2 mt-4 p-3 bg-green-50 text-green-700 rounded-md text-sm">
          <CheckCircle className="h-4 w-4 shrink-0" />
          {success}
        </div>
      )}

      <div className="mt-8 p-4 border rounded-lg bg-card">
        <h2 className="font-medium mb-2 flex items-center gap-2">
          <FileText className="h-4 w-4" /> What happens after upload?
        </h2>
        <ol className="text-sm text-muted-foreground space-y-1 list-decimal list-inside">
          <li>PDF text is extracted and PII is scrubbed</li>
          <li>AI extracts deal fields (sector, revenue, EBITDA, etc.)</li>
          <li>Deal is scored against your screening criteria</li>
          <li>Review the scored deal and make a pass/pursue decision</li>
        </ol>
      </div>
    </div>
  );
}
