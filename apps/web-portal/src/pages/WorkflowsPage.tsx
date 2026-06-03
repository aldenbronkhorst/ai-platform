import { useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Play,
  BookOpen,
  DollarSign,
  Users,
  Layers,
  RefreshCw,
} from "lucide-react";
import type { WorkflowCardData } from "../types";
import { WorkflowCard } from "../components/ui/WorkflowCard";
import { GlassPanel } from "../components/ui/GlassPanel";
import { GlassButton } from "../components/ui/GlassButton";
import { GlassInput } from "../components/ui/GlassInput";
import { APIM_BASE_URL } from "../hooks/useApi";

const BUSINESS_WORKFLOWS: WorkflowCardData[] = [
  {
    id: "credit_note_review",
    title: "Review Credit Note",
    description: "Audit Odoo credit notes against customer claim history and return logs to detect discrepancies.",
    category: "finance",
    inputs: [
      { name: "credit_note_id", label: "Odoo Credit Note Reference", type: "text", placeholder: "e.g., R-2026-00012" },
      { name: "claim_ref", label: "Customer Claim Reference", type: "text", placeholder: "e.g., CLM-9921" },
    ],
  },
  {
    id: "compare_odoo_to_pdf",
    title: "Compare Odoo to PDFs",
    description: "Perform automated cross-verification between Odoo invoices and uploaded raw purchase order PDFs.",
    category: "finance",
    inputs: [
      { name: "invoice_id", label: "Odoo Invoice Reference", type: "text", placeholder: "e.g., INV/2026/0045" },
      { name: "pdf_doc", label: "Select PO Document Reference", type: "text", placeholder: "e.g., PO-ALDEN-2026" },
    ],
  },
  {
    id: "supplier_statement_check",
    title: "Check Supplier Statement",
    description: "Verify statement line-items against Odoo ledger accounts and flag missing or mismatched invoices.",
    category: "finance",
    inputs: [
      { name: "supplier", label: "Supplier / Partner Account", type: "text", placeholder: "e.g., Microsoft South Africa" },
      { name: "statement_date", label: "Statement Close Date", type: "date" },
    ],
  },
  {
    id: "invoice_pricing_review",
    title: "Review Invoice Pricing",
    description: "Compare active Odoo invoice lines against verified contract pricing tables and contract terms.",
    category: "finance",
    inputs: [
      { name: "partner_id", label: "Select Customer Account", type: "text", placeholder: "e.g., Lots Lots More Ltd" },
      { name: "contract_id", label: "Contract Reference ID", type: "text", placeholder: "e.g., CON-9002-PROD" },
    ],
  },
  {
    id: "attendance_review",
    title: "Review Attendance",
    description: "Examine shift timesheets and biometric check-ins to review hours worked and overtime requests.",
    category: "hr",
    inputs: [
      { name: "date_range_start", label: "Period Start Date", type: "date" },
      { name: "date_range_end", label: "Period End Date", type: "date" },
      { name: "department", label: "Target Department", type: "select", options: ["All Departments", "Operations", "Finance", "Logistics", "Sales"] },
    ],
  },
  {
    id: "attendance_exceptions",
    title: "Summarise Attendance Exceptions",
    description: "Automatically surface biometric discrepancies, late check-ins, or unapproved leave instances.",
    category: "hr",
    inputs: [
      { name: "date", label: "Exception Review Date", type: "date" },
      { name: "team_lead", label: "Escalation Team Lead", type: "text", placeholder: "e.g., Alden Bronkhorst" },
    ],
  },
  {
    id: "missing_clockins",
    title: "Check Missing Clock-ins",
    description: "Audit Odoo timesheets against door access control logs to detect missing check-ins/check-outs.",
    category: "hr",
    inputs: [
      { name: "employee_id", label: "Employee Name / ID", type: "text", placeholder: "e.g., Alden Bronkhorst" },
      { name: "period", label: "Review Period", type: "select", options: ["Current Week", "Previous Week", "Current Month"] },
    ],
  },
  {
    id: "attendance_report",
    title: "Prepare Attendance Report",
    description: "Generate structured, executive-ready attendance summary reports in formatted PDF or XLSX sheets.",
    category: "hr",
    inputs: [
      { name: "month", label: "Report Month", type: "select", options: ["May 2026", "April 2026", "March 2026"] },
      { name: "format", label: "Report Output Format", type: "select", options: ["Standard PDF Format", "Formatted Excel Sheet (XLSX)"] },
    ],
  },
  {
    id: "outstanding_tasks",
    title: "Review Outstanding Tasks",
    description: "Audit pending Odoo operational tasks and backlog, highlighting critical path delivery blockages.",
    category: "operations",
    inputs: [
      { name: "priority_level", label: "Minimum Backlog Priority", type: "select", options: ["Medium & High Priority", "High & Critical Only", "All Tasks"] },
      { name: "owner", label: "Task Assignee", type: "text", placeholder: "e.g., Alden Bronkhorst" },
    ],
  },
  {
    id: "missing_attachments",
    title: "Check Missing Attachments",
    description: "Verify that Odoo sale orders and shipments have complete legal and shipping documents attached.",
    category: "operations",
    inputs: [
      { name: "model", label: "Odoo Business Document", type: "select", options: ["sale.order", "account.move", "purchase.order"] },
      { name: "date_start", label: "Created Since", type: "date" },
    ],
  },
  {
    id: "customer_account_summary",
    title: "Summarise Customer Account",
    description: "Generate a 360° overview of a customer account including order history, balances, and messages.",
    category: "operations",
    inputs: [
      { name: "customer_ref", label: "Select Customer / Partner Name", type: "text", placeholder: "e.g., Lots Lots More Ltd" },
    ],
  },
];

const CATEGORY_META: Record<string, { icon: typeof DollarSign; label: string }> = {
  finance: { icon: DollarSign, label: "Finance Ledger Operations" },
  hr: { icon: Users, label: "HR & Timesheet Management" },
  operations: { icon: Layers, label: "Backlog & Operations" },
};

interface WorkflowsPageProps {
  accessToken: string;
  onLaunchChat: (workflowId: string) => void;
}

interface WorkflowOutcome {
  success: boolean;
  message: string;
  details?: unknown;
  jobId?: string;
  artifactId?: string;
}

type WorkflowPayload = {
  create_job: boolean;
  job_title: string;
  identity_mode: string;
  model?: string;
  domain?: unknown[];
  limit?: number;
};

export function WorkflowsPage({ accessToken, onLaunchChat }: WorkflowsPageProps) {
  const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowCardData | null>(null);
  const [workflowInputs, setWorkflowInputs] = useState<Record<string, string>>({});
  const [isWorkflowRunning, setIsWorkflowRunning] = useState(false);
  const [workflowOutcome, setWorkflowOutcome] = useState<WorkflowOutcome | null>(null);

  const handleRunWorkflow = async () => {
    if (!selectedWorkflow || !accessToken) return;
    setIsWorkflowRunning(true);
    setWorkflowOutcome(null);

    try {
      const endpoint = "/tools/odoo/search-read";
      const payload: WorkflowPayload = {
        create_job: true,
        job_title: `${selectedWorkflow.title}: ${Object.values(workflowInputs).join(", ")}`,
        identity_mode: "user-delegated",
      };

      if (selectedWorkflow.id === "attendance_review" || selectedWorkflow.id === "attendance_exceptions") {
        payload.model = "hr.attendance";
        payload.limit = 5;
      } else if (selectedWorkflow.id === "credit_note_review") {
        payload.model = "account.move";
        payload.domain = [["move_type", "=", "out_refund"]];
        payload.limit = 3;
      } else if (selectedWorkflow.id === "outstanding_tasks") {
        payload.model = "project.task";
        payload.limit = 5;
      } else {
        payload.model = "res.partner";
        payload.limit = 1;
      }

      const response = await fetch(`${APIM_BASE_URL}${endpoint}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      const data = await response.json() as {
        records?: unknown;
        detail?: string;
        _job?: {
          job_id?: string;
          artifact_id?: string;
        };
      };
      if (response.ok) {
        setWorkflowOutcome({
          success: true,
          message: `Successfully executed the ${selectedWorkflow.title} workflow.`,
          details: data.records || data,
          jobId: data._job?.job_id,
          artifactId: data._job?.artifact_id,
        });
      } else {
        setWorkflowOutcome({
          success: false,
          message: data.detail || "Workflow execution failed. Ensure Odoo is connected.",
        });
      }
    } catch (err: unknown) {
      setWorkflowOutcome({ success: false, message: `Connection error: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setIsWorkflowRunning(false);
    }
  };

  const handleLaunchContextualChat = (workflow: WorkflowCardData) => {
    onLaunchChat(workflow.id);
  };

  if (selectedWorkflow) {
    return (
      <div className="max-w-2xl mx-auto space-y-6 animate-fade-in">
        <button
          onClick={() => {
            setSelectedWorkflow(null);
            setWorkflowOutcome(null);
          }}
          className="flex items-center gap-1.5 text-xs text-muted hover-text-default select-none"
        >
          <ArrowLeft className="w-4 h-4" /> Back to Workflows
        </button>

        <GlassPanel className="p-6 rounded-3xl space-y-6">
          <div>
            <h2 className="text-lg font-bold text-default mb-1.5">{selectedWorkflow.title}</h2>
            <p className="text-xs text-muted leading-relaxed">{selectedWorkflow.description}</p>
          </div>

          <div className="space-y-4">
            {selectedWorkflow.inputs.map((input) => (
              <div key={input.name}>
                <label className="text-xs text-muted font-bold block mb-1.5 uppercase">
                  {input.label}
                </label>
                {input.type === "select" ? (
                  <select
                    value={workflowInputs[input.name] || ""}
                    onChange={(e) =>
                      setWorkflowInputs((prev) => ({ ...prev, [input.name]: e.target.value }))
                    }
                    className="w-full px-4 py-3 bg-transparent border border-default rounded-xl focus:outline-none focus:border-soft text-xs text-default"
                  >
                    <option value="" className="bg-canvas">
                      Choose Options...
                    </option>
                    {input.options?.map((opt) => (
                      <option key={opt} value={opt} className="bg-canvas">
                        {opt}
                      </option>
                    ))}
                  </select>
                ) : (
                  <GlassInput
                    type={input.type}
                    value={workflowInputs[input.name] || ""}
                    onChange={(e) =>
                      setWorkflowInputs((prev) => ({ ...prev, [input.name]: e.target.value }))
                    }
                    placeholder={input.placeholder}
                  />
                )}
              </div>
            ))}
          </div>

          <div className="flex gap-4 pt-4 border-t border-default">
            <GlassButton
              onClick={() => handleLaunchContextualChat(selectedWorkflow)}
              className="flex-1"
            >
              Ask AI
            </GlassButton>
            <GlassButton
              onClick={handleRunWorkflow}
              disabled={isWorkflowRunning}
              className="flex-1"
            >
              {isWorkflowRunning ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" /> Executing...
                </>
              ) : (
                <>
                  <Play className="w-4 h-4" /> Execute Workflow
                </>
              )}
            </GlassButton>
          </div>
        </GlassPanel>

        {workflowOutcome && (
          <div
            className={`p-5 border rounded-2xl flex items-start gap-4 text-sm ${
              workflowOutcome.success
                ? "bg-[var(--color-success)]/10 border-[var(--color-success)]/25 text-[var(--color-success)]"
                : "bg-[var(--color-danger)]/10 border-[var(--color-danger)]/25 text-[var(--color-danger)]"
            }`}
          >
            {workflowOutcome.success ? (
              <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" />
            ) : (
              <XCircle className="w-5 h-5 shrink-0 mt-0.5" />
            )}
            <div className="space-y-2 flex-1">
              <p className="font-semibold text-default">
                {workflowOutcome.success ? "Execution Completed" : "Execution Failed"}
              </p>
              <p className="opacity-90">{workflowOutcome.message}</p>
              {workflowOutcome.success && (
                <div className="mt-3 p-3 bg-subtle border border-default rounded-xl text-xs space-y-1.5 font-mono select-text text-default">
                  <p>
                    <span className="text-muted">Platform Job Reference:</span>{" "}
                    {workflowOutcome.jobId || "None Created"}
                  </p>
                  <p>
                    <span className="text-muted">Secure Document Artifact:</span>{" "}
                    {workflowOutcome.artifactId || "None Generated"}
                  </p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto space-y-8 select-none animate-fade-in">
      <GlassPanel className="p-8 rounded-3xl flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-default mb-2">
            Automated Business Workflows
          </h2>
          <p className="text-sm text-muted max-w-2xl">
            Execute structured operational tasks on behalf of your connected accounts.
          </p>
        </div>
        <BookOpen className="w-12 h-12 text-soft shrink-0" />
      </GlassPanel>

      {(["finance", "hr", "operations"] as const).map((cat) => {
        const meta = CATEGORY_META[cat];
        const Icon = meta.icon;
        return (
          <div key={cat} className="space-y-4">
            <h3 className="text-xs uppercase tracking-widest text-muted font-bold flex items-center gap-2">
              <Icon className="w-4 h-4" />
              {meta.label}
            </h3>
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
              {BUSINESS_WORKFLOWS.filter((card) => card.category === cat).map((workflow) => (
                <WorkflowCard
                  key={workflow.id}
                  workflow={workflow}
                  onSelect={(w) => {
                    setSelectedWorkflow(w);
                    setWorkflowInputs({});
                    setWorkflowOutcome(null);
                  }}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
