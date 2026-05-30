import { useState } from "react";
import type { MemoryCandidate } from "../../types";

interface MemoryProposalProps {
  candidate: MemoryCandidate;
  accessToken: string;
  sessionId: string;
  onDismiss: () => void;
  onSaved: () => void;
}

const APIM_BASE_URL = import.meta.env.VITE_APIM_BASE_URL || "https://apim-ai-platform-prod-san-001.azure-api.net";

export function MemoryProposal({ candidate, accessToken, sessionId, onDismiss, onSaved }: MemoryProposalProps) {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(candidate.title);
  const [body, setBody] = useState(candidate.body || "");
  const [error, setError] = useState("");

  const doSave = async () => {
    setSaving(true);
    setError("");
    try {
      const updated = { ...candidate, title, body };
      const res = await fetch(`${APIM_BASE_URL}/memories/save-candidate?conversation_id=${sessionId}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(updated),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => null);
        throw new Error(errBody?.detail || `Save failed (${res.status})`);
      }
      setSaved(true);
      setTimeout(onSaved, 2000);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (saved) {
    return (
      <div className="mt-2 bg-surface border border-[var(--color-success)]/30 rounded-xl px-4 py-3 text-xs text-[var(--color-success)]">
        ✓ Memory saved for future conversations
      </div>
    );
  }

  const modeLabel: Record<string, string> = {
    auto: "Auto-saved",
    confirm: "Needs confirmation",
    admin_approval: "Needs admin approval",
  };

  return (
    <div className="mt-2 bg-raised border border-default rounded-xl px-4 py-3 space-y-2">
      <div className="flex items-center justify-between text-[10px] text-muted font-semibold uppercase tracking-wider">
        <span>{candidate.type.replace(/_/g, " ")}</span>
        <span>{modeLabel[candidate.save_mode] || candidate.save_mode}</span>
      </div>

      {editing ? (
        <div className="space-y-2">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full bg-canvas border border-default rounded-lg px-3 py-1.5 text-xs outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
          />
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={2}
            className="w-full bg-canvas border border-default rounded-lg px-3 py-1.5 text-xs outline-none focus:ring-2 focus:ring-[var(--color-accent)] resize-none"
          />
        </div>
      ) : (
        <div>
          <p className="text-xs font-semibold">{title}</p>
          {body && <p className="text-[11px] text-muted mt-0.5">{body}</p>}
        </div>
      )}

      {error && <p className="text-[10px] text-[var(--color-danger)]">{error}</p>}

      {candidate.save_mode !== "auto" && (
        <div className="flex gap-2 pt-1">
          {editing ? (
            <>
              <button onClick={() => setEditing(false)} className="text-[11px] px-3 py-1.5 bg-surface border border-default rounded-lg hover-bg-surface transition-all">
                Cancel
              </button>
              <button onClick={doSave} disabled={saving} className="text-[11px] px-3 py-1.5 glass-btn rounded-lg disabled:opacity-50 transition-all">
                {saving ? "Saving..." : "Save"}
              </button>
            </>
          ) : (
            <>
              <button onClick={doSave} disabled={saving} className="text-[11px] px-3 py-1.5 glass-btn rounded-lg disabled:opacity-50 transition-all">
                {saving ? "Saving..." : "Save"}
              </button>
              <button onClick={() => setEditing(true)} className="text-[11px] px-3 py-1.5 bg-surface border border-default rounded-lg hover-bg-surface transition-all">
                Edit
              </button>
              <button onClick={onDismiss} className="text-[11px] px-3 py-1.5 text-muted hover:text-default transition-all">
                Dismiss
              </button>
            </>
          )}
        </div>
      )}

      {candidate.save_mode === "admin_approval" && (
        <p className="text-[10px] text-[var(--color-warning)]">Saved as draft for administrator review.</p>
      )}
    </div>
  );
}
