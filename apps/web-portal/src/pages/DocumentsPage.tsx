import { useState, useEffect, useCallback } from "react";
import { FileText, HardDrive, Download, RefreshCw } from "lucide-react";
import { GlassPanel } from "../components/ui/GlassPanel";
import { APIM_BASE_URL } from "../hooks/useApi";

interface DocumentsPageProps {
  accessToken: string;
}

interface Artifact {
  id: string;
  job_id: string | null;
  artifact_type: string;
  filename: string;
  mime_type: string;
}

export function DocumentsPage({ accessToken }: DocumentsPageProps) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const fetchArtifacts = useCallback(async () => {
    if (!accessToken) return;
    await Promise.resolve();
    setIsLoading(true);
    try {
      const res = await fetch(`${APIM_BASE_URL}/artifacts`, {
        headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
      });
      if (res.ok) {
        const data = await res.json();
        setArtifacts(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error("Failed to fetch artifacts:", err);
    } finally {
      setIsLoading(false);
    }
  }, [accessToken]);

  const handleDownload = async (artifactId: string) => {
    try {
      const res = await fetch(`${APIM_BASE_URL}/artifacts/${artifactId}/download`, {
        headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
      });
      if (!res.ok) return;
      const data = await res.json() as { download_url?: string };
      if (data.download_url) window.open(data.download_url, "_blank", "noopener,noreferrer");
    } catch (err) {
      console.error("Failed to download artifact:", err);
    }
  };

  useEffect(() => {
    if (accessToken) void Promise.resolve().then(fetchArtifacts);
  }, [accessToken, fetchArtifacts]);

  return (
    <div className="max-w-6xl mx-auto space-y-6 animate-fade-in">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-default">Documents Vault</h2>
          <p className="text-sm text-muted mt-1">
            Access secure Odoo outputs, supplier statements, and compiled reports.
          </p>
        </div>
        <button
          onClick={fetchArtifacts}
          disabled={isLoading}
          className="p-2 bg-surface border border-default hover-bg-surface rounded-xl transition-all"
        >
          <RefreshCw className={`w-4 h-4 text-muted ${isLoading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-20 text-muted">Loading documents...</div>
      ) : artifacts.length === 0 ? (
        <div className="border border-dashed border-default rounded-2xl text-center py-16 text-muted animate-fade-in">
          <FileText className="w-10 h-10 text-soft mb-3 mx-auto" />
          <p className="font-semibold text-default">No documents found</p>
          <p className="text-xs text-soft max-w-sm mx-auto mt-1">
            Executed workflows generate Excel and PDF audit summaries.
          </p>
        </div>
      ) : (
        <div className="grid md:grid-cols-3 gap-6 select-text animate-fade-in">
          {artifacts.map((art) => (
            <GlassPanel key={art.id} className="p-5 rounded-2xl flex flex-col justify-between">
              <div>
                <div className="flex justify-between items-start mb-3">
                  <span className="bg-surface text-muted border border-default px-2.5 py-0.5 rounded-full text-[10px] font-mono uppercase">
                    {art.artifact_type}
                  </span>
                  <HardDrive className="w-4 h-4 text-soft" />
                </div>
                <h4 className="font-semibold text-default truncate text-sm" title={art.filename}>
                  {art.filename}
                </h4>
                <p className="text-xs text-muted font-mono mt-1">MIME: {art.mime_type}</p>
              </div>
              <div className="mt-4 pt-3 border-t border-default flex justify-between items-center text-xs">
                <span className="text-muted font-mono text-[10px]">
                  Job: {art.job_id?.slice(0, 8)}...
                </span>
                <button
                  onClick={() => void handleDownload(art.id)}
                  className="text-muted hover:text-default font-semibold tracking-wide flex items-center gap-1 hover:underline"
                >
                  Download
                  <Download className="w-3.5 h-3.5" />
                </button>
              </div>
            </GlassPanel>
          ))}
        </div>
      )}
    </div>
  );
}
