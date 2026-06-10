import { useState, useEffect, useCallback } from "react";
import { ClipboardList, RefreshCw } from "lucide-react";
import { GlassPanel } from "../components/ui/GlassPanel";
import { APIM_BASE_URL } from "../hooks/useApi";

interface TasksPageProps {
  accessToken: string;
}

interface Task {
  id: string;
  title: string;
  status: string;
  priority: string;
  description: string | null;
  created_at: string;
  due_at: string | null;
}

export function TasksPage({ accessToken }: TasksPageProps) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const fetchTasks = useCallback(async () => {
    if (!accessToken) return;
    await Promise.resolve();
    setIsLoading(true);
    try {
      const res = await fetch(`${APIM_BASE_URL}/tasks`, {
        headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
      });
      if (res.ok) {
        const data = await res.json();
        setTasks(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error("Failed to fetch tasks:", err);
    } finally {
      setIsLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    if (accessToken) void Promise.resolve().then(fetchTasks);
  }, [accessToken, fetchTasks]);

  return (
    <div className="max-w-6xl mx-auto space-y-6 animate-fade-in">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-default">Tasks Tracker</h2>
          <p className="text-sm text-muted mt-1">
            Check Odoo operational tasks, biometric anomalies, and assigned backlogs.
          </p>
        </div>
        <button
          onClick={fetchTasks}
          disabled={isLoading}
          className="p-2 bg-surface border border-default hover-bg-surface rounded-xl transition-all"
        >
          <RefreshCw className={`w-4 h-4 text-muted ${isLoading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-20 text-muted">Loading tasks...</div>
      ) : tasks.length === 0 ? (
        <div className="border border-dashed border-default rounded-2xl text-center py-16 text-muted animate-fade-in">
          <ClipboardList className="w-10 h-10 text-soft mb-3 mx-auto" />
          <p className="font-semibold text-default">No active tasks found</p>
          <p className="text-xs text-soft max-w-sm mx-auto mt-1">
            Biometric clock-in exception audits and claim mismatches appear as tasks here.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 select-text animate-fade-in">
          {tasks.map((task) => (
            <GlassPanel key={task.id} className="p-5 rounded-2xl flex items-center justify-between">
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 rounded-xl bg-surface border border-default flex items-center justify-center text-muted">
                  <ClipboardList className="w-5 h-5" />
                </div>
                <div>
                  <h4 className="font-semibold text-default text-sm">{task.title}</h4>
                  {task.description && (
                    <p className="text-xs text-muted mt-1 max-w-2xl line-clamp-2">{task.description}</p>
                  )}
                  <div className="flex gap-2 items-center mt-1.5 text-[11px] font-mono text-muted">
                    <span>ID: {task.id.slice(0, 8)}...</span>
                    <span>•</span>
                    <span>Created: {new Date(task.created_at).toLocaleDateString()}</span>
                    {task.due_at && (
                      <>
                        <span>•</span>
                        <span>Due: {new Date(task.due_at).toLocaleDateString()}</span>
                      </>
                    )}
                    <span>•</span>
                    <span>Priority: {task.priority}</span>
                  </div>
                </div>
              </div>
              <span
                className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase ${
                  task.status === "done"
                    ? "bg-[var(--color-success)]/10 text-[var(--color-success)] border border-[var(--color-success)]/20"
                    : "bg-[var(--color-warning)]/10 text-[var(--color-warning)] border border-[var(--color-warning)]/20"
                }`}
              >
                {task.status}
              </span>
            </GlassPanel>
          ))}
        </div>
      )}
    </div>
  );
}
