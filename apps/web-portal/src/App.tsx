import React, { useState, useEffect, useRef } from "react";
import { useMsal } from "@azure/msal-react";
import { InteractionStatus } from "@azure/msal-browser";
import { loginRequest } from "./authConfig";
import { 
  MessageSquare, 
  Database, 
  FileText, 
  ShieldAlert, 
  Settings, 
  LogOut, 
  User, 
  RefreshCw, 
  Plus, 
  Trash2, 
  Shield, 
  Search, 
  ArrowRight, 
  Bot, 
  CheckCircle2, 
  XCircle, 
  AlertTriangle,
  HardDrive,
  Eye,
  Key,
  ExternalLink,
  BookOpen,
  DollarSign,
  Users,
  Layers,
  ArrowLeft,
  ClipboardList,
  Compass,
  Play,
  ChevronDown,
  Paperclip,
  Mic,
  MicOff,
  CornerDownLeft,
  X
} from "lucide-react";

// API base URL pointing to the production APIM Gateway (which routes to AI Core API)
const APIM_BASE_URL = import.meta.env.VITE_APIM_BASE_URL || "https://apim-ai-platform-prod-san-001.azure-api.net";

// Developer local mock configuration (strictly local-only)
const ENABLE_LOCAL_MOCK = 
  import.meta.env.VITE_ENABLE_LOCAL_MOCK_AUTH === "true" && 
  (typeof window !== "undefined" && 
    (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"));

interface UserProfile {
  id?: string;
  email: string;
  displayName: string;
  roles: string[];
}

interface ChatSession {
  id: string;
  title: string;
  status: string;
  workflow_context?: string;
  created_at: string;
  last_message_at: string;
}

interface ChatMessage {
  id: string;
  chat_session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  model_name?: string;
  metadata_json?: any;
}

interface WorkflowCard {
  id: string;
  title: string;
  description: string;
  category: "finance" | "hr" | "operations";
  inputs: Array<{
    name: string;
    label: string;
    type: "date" | "select" | "text";
    options?: string[];
    placeholder?: string;
  }>;
}

const BUSINESS_WORKFLOWS: WorkflowCard[] = [
  // Finance
  {
    id: "credit_note_review",
    title: "Review Credit Note",
    description: "Audit Odoo credit notes against customer claim history and return logs to detect discrepancies.",
    category: "finance",
    inputs: [
      { name: "credit_note_id", label: "Odoo Credit Note Reference", type: "text", placeholder: "e.g., R-2026-00012" },
      { name: "claim_ref", label: "Customer Claim Reference", type: "text", placeholder: "e.g., CLM-9921" }
    ]
  },
  {
    id: "compare_odoo_to_pdf",
    title: "Compare Odoo to PDFs",
    description: "Perform automated cross-verification between Odoo invoices and uploaded raw purchase order PDFs.",
    category: "finance",
    inputs: [
      { name: "invoice_id", label: "Odoo Invoice Reference", type: "text", placeholder: "e.g., INV/2026/0045" },
      { name: "pdf_doc", label: "Select PO Document Reference", type: "text", placeholder: "e.g., PO-ALDEN-2026" }
    ]
  },
  {
    id: "supplier_statement_check",
    title: "Check Supplier Statement",
    description: "Verify statement line-items against Odoo ledger accounts and flag missing or mismatched invoices.",
    category: "finance",
    inputs: [
      { name: "supplier", label: "Supplier / Partner Account", type: "text", placeholder: "e.g., Microsoft South Africa" },
      { name: "statement_date", label: "Statement Close Date", type: "date" }
    ]
  },
  {
    id: "invoice_pricing_review",
    title: "Review Invoice Pricing",
    description: "Compare active Odoo invoice lines against verified contract pricing tables and contract terms.",
    category: "finance",
    inputs: [
      { name: "partner_id", label: "Select Customer Account", type: "text", placeholder: "e.g., Lots Lots More Ltd" },
      { name: "contract_id", label: "Contract Reference ID", type: "text", placeholder: "e.g., CON-9002-PROD" }
    ]
  },
  // HR / Attendance
  {
    id: "attendance_review",
    title: "Review Attendance",
    description: "Examine shift timesheets and biometric check-ins to review hours worked and overtime requests.",
    category: "hr",
    inputs: [
      { name: "date_range_start", label: "Period Start Date", type: "date" },
      { name: "date_range_end", label: "Period End Date", type: "date" },
      { name: "department", label: "Target Department", type: "select", options: ["All Departments", "Operations", "Finance", "Logistics", "Sales"] }
    ]
  },
  {
    id: "attendance_exceptions",
    title: "Summarise Attendance Exceptions",
    description: "Automatically surface biometric discrepancies, late check-ins, or unapproved leave instances.",
    category: "hr",
    inputs: [
      { name: "date", label: "Exception Review Date", type: "date" },
      { name: "team_lead", label: "Escalation Team Lead", type: "text", placeholder: "e.g., Alden Bronkhorst" }
    ]
  },
  {
    id: "missing_clockins",
    title: "Check Missing Clock-ins",
    description: "Audit Odoo timesheets against door access control logs to detect missing check-ins/check-outs.",
    category: "hr",
    inputs: [
      { name: "employee_id", label: "Employee Name / ID", type: "text", placeholder: "e.g., Alden Bronkhorst" },
      { name: "period", label: "Review Period", type: "select", options: ["Current Week", "Previous Week", "Current Month"] }
    ]
  },
  {
    id: "attendance_report",
    title: "Prepare Attendance Report",
    description: "Generate structured, executive-ready attendance summary reports in formatted PDF or XLSX sheets.",
    category: "hr",
    inputs: [
      { name: "month", label: "Report Month", type: "select", options: ["May 2026", "April 2026", "March 2026"] },
      { name: "format", label: "Report Output Format", type: "select", options: ["Standard PDF Format", "Formatted Excel Sheet (XLSX)"] }
    ]
  },
  // Operations
  {
    id: "outstanding_tasks",
    title: "Review Outstanding Tasks",
    description: "Audit pending Odoo operational tasks and backlog, highlighting critical path delivery blockages.",
    category: "operations",
    inputs: [
      { name: "priority_level", label: "Minimum Backlog Priority", type: "select", options: ["Medium & High Priority", "High & Critical Only", "All Tasks"] },
      { name: "owner", label: "Task Assignee", type: "text", placeholder: "e.g., Alden Bronkhorst" }
    ]
  },
  {
    id: "missing_attachments",
    title: "Check Missing Attachments",
    description: "Verify that Odoo sale orders and shipments have complete legal and shipping documents attached.",
    category: "operations",
    inputs: [
      { name: "model", label: "Odoo Business Document", type: "select", options: ["sale.order", "account.move", "purchase.order"] },
      { name: "date_start", label: "Created Since", type: "date" }
    ]
  },
  {
    id: "customer_account_summary",
    title: "Summarise Customer Account",
    description: "Generate a 360° overview of a customer account including order history, balances, and messages.",
    category: "operations",
    inputs: [
      { name: "customer_ref", label: "Select Customer / Partner Name", type: "text", placeholder: "e.g., Lots Lots More Ltd" }
    ]
  }
];

export default function App({ startupAuthError }: { startupAuthError: string | null }) {
  const { instance, accounts, inProgress } = useMsal();

  // Navigation Tabs
  const [activeTab, setActiveTab] = useState<string>("workflows");

  // Profile overlay menu states
  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState<boolean>(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [showDiagnostics, setShowDiagnostics] = useState<boolean>(false);

  // Local Mock Auth States
  const [localMockAuthenticated, setLocalMockAuthenticated] = useState<boolean>(false);
  const [localMockUser, setLocalMockUser] = useState<UserProfile | null>(null);

  // Unified active user
  const [activeUser, setActiveUser] = useState<UserProfile | null>(null);
  const [accessToken, setAccessToken] = useState<string>("");

  // Multiple Chat Sessions States
  const [chatSessions, setChatSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
  const [isSessionsLoading, setIsSessionsLoading] = useState<boolean>(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [isMessagesLoading, setIsMessagesLoading] = useState<boolean>(false);
  const [chatInput, setChatInput] = useState<string>("");
  const [isChatSending, setIsChatSending] = useState<boolean>(false);

  // Unused Job/Blob technical detail toggle
  const [expandedTraceMsgs, setExpandedTraceMsgs] = useState<Record<string, boolean>>({});

  // File Upload State
  const [attachedFiles, setAttachedFiles] = useState<Array<{ file: File; id?: string; uploading: boolean }>>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Voice Input State
  const [voiceState, setVoiceState] = useState<"idle" | "listening" | "processing" | "unsupported" | "denied">("idle");
  const recognitionRef = useRef<any>(null);

  // Workflow orchestration states
  const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowCard | null>(null);
  const [workflowInputs, setWorkflowInputs] = useState<Record<string, string>>({});
  const [isWorkflowRunning, setIsWorkflowRunning] = useState<boolean>(false);
  const [workflowOutcome, setWorkflowOutcome] = useState<any | null>(null);

  // Connected Accounts States
  const [odooStatus, setOdooStatus] = useState<any>({ status: "not_connected" });
  const [isStatusLoading, setIsStatusLoading] = useState<boolean>(false);
  const [isConnectOpen, setIsConnectOpen] = useState<boolean>(false);
  const [isRotateOpen, setIsRotateOpen] = useState<boolean>(false);
  const [isConnecting, setIsConnecting] = useState<boolean>(false);
  const [isTesting, setIsTesting] = useState<boolean>(false);
  const [testResult, setTestResult] = useState<any>(null);

  // Odoo Form inputs
  const [odooUrl, setOdooUrl] = useState<string>("https://odoo.lotslotsmore.com");
  const [odooDb, setOdooDb] = useState<string>("Lots Lots More Production");
  const [odooUsername, setOdooUsername] = useState<string>("alden@lotslotsmore.com");
  const [odooApiKey, setOdooApiKey] = useState<string>("");

  // Audit Logs States
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  const [isAuditLoading, setIsAuditLoading] = useState<boolean>(false);
  const [auditFilter, setAuditFilter] = useState<string>("");
  const [inspectLog, setInspectLog] = useState<any | null>(null);

  // Jobs States
  const [jobs, setJobs] = useState<any[]>([]);
  const [isJobsLoading, setIsJobsLoading] = useState<boolean>(false);

  // Artifacts (Documents) States
  const [artifacts, setArtifacts] = useState<any[]>([]);
  const [isArtifactsLoading, setIsArtifactsLoading] = useState<boolean>(false);

  // Synchronize active authentication session
  useEffect(() => {
    const activeAccount = instance.getActiveAccount() || (accounts.length > 0 ? accounts[0] : null);
    
    if (activeAccount) {
      const idTokenClaims = activeAccount.idTokenClaims as any;
      const roles = idTokenClaims?.roles || ["AIPlatform.User"];

      setActiveUser({
        email: activeAccount.username,
        displayName: activeAccount.name || activeAccount.username,
        roles: roles
      });
      
      setAuthError(null);

      // Acquire access token silently
      instance.acquireTokenSilent({
        ...loginRequest,
        account: activeAccount
      }).then(response => {
        setAccessToken(response.accessToken);
      }).catch(err => {
        console.warn("Silent token acquisition failed, prompting login:", err);
        setAuthError(`Token acquisition failed. Please sign in again.`);
      });
    } else if (ENABLE_LOCAL_MOCK && localMockAuthenticated && localMockUser) {
      setActiveUser(localMockUser);
      setAccessToken("mock-local-token");
      setAuthError(null);
    } else {
      setActiveUser(null);
      setAccessToken("");
    }
  }, [accounts, localMockAuthenticated, localMockUser, instance]);

  // Fetch Chat Sessions on load
  useEffect(() => {
    if (accessToken) {
      fetchChatSessions();
      fetchOdooStatus();
    }
  }, [accessToken]);

  // Fetch Odoo, Audit, Jobs, and Artifacts on active Tab changes
  useEffect(() => {
    if (!accessToken) return;
    if (activeTab === "connected-accounts") fetchOdooStatus();
    if (activeTab === "audit") fetchAuditLogs();
    if (activeTab === "jobs") fetchJobs();
    if (activeTab === "artifacts") fetchArtifacts();
  }, [activeTab, accessToken]);

  // Sync active message list when chat session changes
  useEffect(() => {
    if (activeSession && accessToken) {
      fetchSessionMessages(activeSession.id);
    } else {
      setChatMessages([]);
    }
  }, [activeSession]);

  const getRequestHeaders = () => {
    return {
      "Authorization": `Bearer ${accessToken}`,
      "Content-Type": "application/json"
    };
  };

  const hasRole = (allowedRoles: string[]) => {
    if (!activeUser) return false;
    if (activeUser.roles.includes("AIPlatform.Admin")) return true;
    return activeUser.roles.some(r => allowedRoles.includes(r));
  };

  // --- VOICE INPUT (WEB SPEECH API) ---
  useEffect(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setVoiceState("unsupported");
    } else {
      const rec = new SpeechRecognition();
      rec.continuous = false;
      rec.interimResults = false;
      rec.lang = "en-US";

      rec.onstart = () => setVoiceState("listening");
      rec.onresult = (event: any) => {
        const transcript = event.results[0][0].transcript;
        setChatInput(prev => (prev ? prev + " " + transcript : transcript));
        setVoiceState("processing");
      };
      rec.onerror = (e: any) => {
        console.error("Speech error:", e);
        if (e.error === "not-allowed") {
          setVoiceState("denied");
        } else {
          setVoiceState("idle");
        }
      };
      rec.onend = () => {
        setVoiceState(prev => prev === "listening" || prev === "processing" ? "idle" : prev);
      };
      recognitionRef.current = rec;
    }
  }, []);

  const handleToggleVoice = () => {
    if (voiceState === "unsupported") return;
    if (voiceState === "listening") {
      recognitionRef.current.stop();
    } else {
      try {
        recognitionRef.current.start();
      } catch (err) {
        console.error("Could not start recognition:", err);
      }
    }
  };

  // --- MULTIPLE CHAT SESSIONS API ---
  const fetchChatSessions = async () => {
    if (!accessToken) return;
    setIsSessionsLoading(true);
    try {
      const response = await fetch(`${APIM_BASE_URL}/chat/sessions`, {
        headers: getRequestHeaders()
      });
      if (response.ok) {
        const data = await response.json();
        setChatSessions(data);
        if (data.length > 0 && !activeSession) {
          setActiveSession(data[0]);
        }
      }
    } catch (err) {
      console.error("Failed to fetch chat sessions:", err);
    } finally {
      setIsSessionsLoading(false);
    }
  };

  const createNewChat = async (workflowContext?: string) => {
    if (!accessToken) return;
    try {
      const title = workflowContext ? `${BUSINESS_WORKFLOWS.find(w=>w.id===workflowContext)?.title} Review` : "New Chat";
      const response = await fetch(`${APIM_BASE_URL}/chat/sessions`, {
        method: "POST",
        headers: getRequestHeaders(),
        body: JSON.stringify({ title, workflow_context: workflowContext })
      });
      if (response.ok) {
        const newSess = await response.json();
        setChatSessions(prev => [newSess, ...prev]);
        setActiveSession(newSess);
        setActiveTab("chat");
      }
    } catch (err) {
      console.error("Failed to create new chat session:", err);
    }
  };

  const fetchSessionMessages = async (sid: string) => {
    setIsMessagesLoading(true);
    try {
      const response = await fetch(`${APIM_BASE_URL}/chat/sessions/${sid}/messages`, {
        headers: getRequestHeaders()
      });
      if (response.ok) {
        const data = await response.json();
        setChatMessages(data);
      }
    } catch (err) {
      console.error("Failed to fetch session messages:", err);
    } finally {
      setIsMessagesLoading(false);
    }
  };

  const deleteChatSession = async (sid: string) => {
    if (!confirm("Are you sure you want to archive/delete this chat session?")) return;
    try {
      const response = await fetch(`${APIM_BASE_URL}/chat/sessions/${sid}`, {
        method: "DELETE",
        headers: getRequestHeaders()
      });
      if (response.ok) {
        setChatSessions(prev => prev.filter(s => s.id !== sid));
        if (activeSession?.id === sid) {
          setActiveSession(null);
        }
        fetchChatSessions();
      }
    } catch (err) {
      console.error("Delete session failed:", err);
    }
  };

  // --- FILE UPLOADS IN CHAT ---
  const handleTriggerUpload = () => {
    fileInputRef.current?.click();
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || !accessToken) return;

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      if (file.size > 15 * 1024 * 1024) {
        alert(`File ${file.name} exceeds 15MB limit.`);
        continue;
      }

      const tempId = Math.random().toString();
      setAttachedFiles(prev => [...prev, { file, id: tempId, uploading: true }]);

      const formData = new FormData();
      formData.append("file", file);
      formData.append("artifact_type", "job-file");
      formData.append("filename", file.name);
      formData.append("mime_type", file.type || "application/octet-stream");

      try {
        const response = await fetch(`${APIM_BASE_URL}/artifacts`, {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${accessToken}`
          },
          body: formData
        });
        if (response.ok) {
          const art = await response.json();
          setAttachedFiles(prev => prev.map(f => f.id === tempId ? { file, id: art.id, uploading: false } : f));
        } else {
          setAttachedFiles(prev => prev.filter(f => f.id !== tempId));
          alert(`Failed to upload ${file.name}.`);
        }
      } catch (err) {
        setAttachedFiles(prev => prev.filter(f => f.id !== tempId));
        console.error("File upload error:", err);
      }
    }
  };

  const handleRemoveFile = (id: string) => {
    setAttachedFiles(prev => prev.filter(f => f.id !== id));
  };

  // --- POST MESSAGE & CHAT ASSISTANT ---
  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if ((!chatInput.trim() && attachedFiles.length === 0) || !accessToken || !activeSession) return;

    const userMsgContent = chatInput;
    setChatInput("");
    setIsChatSending(true);

    const artIds = attachedFiles.filter(f => !f.uploading && f.id).map(f => f.id as string);
    setAttachedFiles([]);

    const tempUserMsg: ChatMessage = {
      id: Math.random().toString(),
      chat_session_id: activeSession.id,
      role: "user",
      content: userMsgContent,
      created_at: new Date().toISOString()
    };
    setChatMessages(prev => [...prev, tempUserMsg]);

    try {
      const response = await fetch(`${APIM_BASE_URL}/chat/sessions/${activeSession.id}/messages`, {
        method: "POST",
        headers: getRequestHeaders(),
        body: JSON.stringify({
          content: userMsgContent,
          artifact_ids: artIds,
          workflow_context: activeSession.workflow_context
        })
      });
      if (response.ok) {
        const botMsg = await response.json();
        setChatMessages(prev => [...prev, botMsg]);
        fetchChatSessions();
      }
    } catch (err) {
      console.error("Failed to send chat message:", err);
    } finally {
      setIsChatSending(false);
    }
  };

  // --- STANDARD ODOO MANAGEMENT CONNECTIONS ---
  const fetchOdooStatus = async () => {
    if (!accessToken) return;
    setIsStatusLoading(true);
    try {
      const response = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/status`, {
        headers: getRequestHeaders()
      });
      if (response.ok) {
        const data = await response.json();
        setOdooStatus(data);
      }
    } catch (err) {
      console.error("Failed to fetch Odoo status:", err);
    } finally {
      setIsStatusLoading(false);
    }
  };

  const handleConnectOdoo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken) return;
    setIsConnecting(true);
    setTestResult(null);
    try {
      const response = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/connect`, {
        method: "POST",
        headers: getRequestHeaders(),
        body: JSON.stringify({
          odoo_url: odooUrl,
          odoo_db: odooDb,
          odoo_username: odooUsername,
          odoo_api_key: odooApiKey
        })
      });
      const data = await response.json();
      if (response.ok) {
        setTestResult({ success: true, message: "Odoo connection established successfully!" });
        setIsConnectOpen(false);
        setOdooApiKey("");
        fetchOdooStatus();
      } else {
        setTestResult({ success: false, message: data.detail || "Connection failed." });
      }
    } catch (err: any) {
      setTestResult({ success: false, message: `Could not reach backend: ${err.message}` });
    } finally {
      setIsConnecting(false);
    }
  };

  const handleTestOdoo = async () => {
    if (!accessToken) return;
    setIsTesting(true);
    setTestResult(null);
    try {
      const response = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/test`, {
        method: "POST",
        headers: getRequestHeaders()
      });
      const data = await response.json();
      if (response.ok) {
        setTestResult({ success: data.status === "connected", message: `Connection state: ${data.status.toUpperCase()}` });
        fetchOdooStatus();
      } else {
        setTestResult({ success: false, message: data.detail || "Verification failed." });
      }
    } catch (err: any) {
      setTestResult({ success: false, message: `Test failed: ${err.message}` });
    } finally {
      setIsTesting(false);
    }
  };

  const handleRotateOdoo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken) return;
    setIsConnecting(true);
    setTestResult(null);
    try {
      const response = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/rotate`, {
        method: "POST",
        headers: getRequestHeaders(),
        body: JSON.stringify({ odoo_api_key: odooApiKey })
      });
      const data = await response.json();
      if (response.ok) {
        setTestResult({ success: true, message: "Odoo credential rotated successfully!" });
        setIsRotateOpen(false);
        setOdooApiKey("");
        fetchOdooStatus();
      } else {
        setTestResult({ success: false, message: data.detail || "Rotation failed." });
      }
    } catch (err: any) {
      setTestResult({ success: false, message: `Rotation failed: ${err.message}` });
    } finally {
      setIsConnecting(false);
    }
  };

  const handleDisconnectOdoo = async () => {
    if (!accessToken) return;
    if (!confirm("Are you sure you want to disconnect Odoo? Credentials will be permanently deleted from Key Vault.")) return;
    setIsStatusLoading(true);
    try {
      const response = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/disconnect`, {
        method: "POST",
        headers: getRequestHeaders()
      });
      if (response.ok) {
        fetchOdooStatus();
        alert("Odoo disconnected successfully.");
      }
    } catch (err) {
      console.error("Disconnect failed:", err);
    } finally {
      setIsStatusLoading(false);
    }
  };

  // --- EXECUTIONS OF GUIDED BUSINESS WORKFLOWS ---
  const handleRunWorkflow = async () => {
    if (!selectedWorkflow || !accessToken) return;
    setIsWorkflowRunning(true);
    setWorkflowOutcome(null);

    try {
      let endpoint = "/tools/odoo/search-read";
      let payload: Record<string, any> = {
        create_job: true,
        job_title: `${selectedWorkflow.title}: ${Object.values(workflowInputs).join(", ")}`,
        identity_mode: "user-delegated"
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
        headers: getRequestHeaders(),
        body: JSON.stringify(payload)
      });
      
      const data = await response.json();
      if (response.ok) {
        setWorkflowOutcome({
          success: true,
          message: `Successfully executed the ${selectedWorkflow.title} workflow.`,
          details: data.records || data,
          jobId: data._job?.job_id,
          artifactId: data._job?.artifact_id
        });
        fetchJobs();
        fetchArtifacts();
      } else {
        setWorkflowOutcome({
          success: false,
          message: data.detail || "Workflow execution failed. Ensure Odoo is connected."
        });
      }
    } catch (err: any) {
      setWorkflowOutcome({ success: false, message: `Connection error: ${err.message}` });
    } finally {
      setIsWorkflowRunning(false);
    }
  };

  const handleLaunchContextualChat = (workflow: WorkflowCard) => {
    createNewChat(workflow.id);
  };

  // --- ADMIN PORTAL SERVICES ---
  const fetchAuditLogs = async () => {
    if (!accessToken) return;
    setIsAuditLoading(true);
    try {
      const response = await fetch(`${APIM_BASE_URL}/audit`, {
        headers: getRequestHeaders()
      });
      if (response.ok) {
        const data = await response.json();
        setAuditLogs(data);
      }
    } catch (err) {
      console.error("Failed to fetch audit logs:", err);
    } finally {
      setIsAuditLoading(false);
    }
  };

  const fetchJobs = async () => {
    if (!accessToken) return;
    setIsJobsLoading(true);
    try {
      const response = await fetch(`${APIM_BASE_URL}/jobs`, {
        headers: getRequestHeaders()
      });
      if (response.ok) {
        const data = await response.json();
        setJobs(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error("Failed to fetch jobs:", err);
    } finally {
      setIsJobsLoading(false);
    }
  };

  const fetchArtifacts = async () => {
    if (!accessToken) return;
    setIsArtifactsLoading(true);
    try {
      const response = await fetch(`${APIM_BASE_URL}/artifacts`, {
        headers: getRequestHeaders()
      });
      if (response.ok) {
        const data = await response.json();
        setArtifacts(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error("Failed to fetch artifacts:", err);
    } finally {
      setIsArtifactsLoading(false);
    }
  };

  const handleSignOut = () => {
    if (localMockAuthenticated) {
      setLocalMockAuthenticated(false);
      setLocalMockUser(null);
    } else {
      instance.logoutRedirect();
    }
  };

  // Render MSAL Interaction Status Loading
  if (inProgress !== InteractionStatus.None) {
    return (
      <div className="flex h-screen bg-[#050811] text-[#f3f4f6] font-sans antialiased overflow-hidden items-center justify-center relative">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(99,102,241,0.08),transparent_50%)]" />
        <div className="relative z-10 text-center space-y-4">
          <RefreshCw className="w-10 h-10 text-indigo-400 animate-spin mx-auto" />
          <p className="text-sm font-semibold tracking-wide text-gray-300">Completing Microsoft sign-in...</p>
        </div>
      </div>
    );
  }

  // --- UNAUTHENTICATED LOGIN SCREEN ---
  if (!activeUser) {
    return (
      <div className="flex h-screen bg-[#050811] text-[#f3f4f6] font-sans antialiased overflow-hidden items-center justify-center relative px-4">
        {/* Glow effect */}
        <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-indigo-500/10 rounded-full blur-[120px] pointer-events-none" />
        <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/10 rounded-full blur-[120px] pointer-events-none" />

        {/* Liquid Glass Login Card */}
        <div className="relative z-10 max-w-md w-full liquid-glass rounded-3xl p-8 text-center space-y-6">
          <div className="mx-auto w-16 h-16 rounded-2xl bg-indigo-600/10 border border-indigo-500/35 flex items-center justify-center">
            <Bot className="w-8 h-8 text-indigo-400" />
          </div>
          
          <div>
            <h2 className="text-2xl font-extrabold text-white tracking-tight">AI Platform</h2>
            <p className="text-xs text-gray-400 mt-2 leading-relaxed">Secure operational portal for Lots Lots More business workflows, Timesheet reviews, and Ledger checks.</p>
          </div>

          {(authError || startupAuthError) && (
            <div className="p-4 border border-rose-500/25 bg-rose-500/10 text-rose-400 text-xs rounded-2xl space-y-3 text-left animate-fade-in">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                <div>
                  <p className="font-bold">Authentication Failed</p>
                  <p className="mt-0.5 opacity-90">{authError || startupAuthError}</p>
                </div>
              </div>
              <button 
                onClick={() => instance.acquireTokenRedirect(loginRequest)}
                className="w-full py-2 bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/40 text-rose-300 font-bold rounded-xl text-[11px] transition-all cursor-pointer"
              >
                Continue Microsoft Permission
              </button>
            </div>
          )}

          <div className="space-y-3 pt-4">
            {/* Real Microsoft Entra ID Login Button */}
            <button 
              onClick={() => instance.loginRedirect(loginRequest)}
              className="w-full py-3 bg-white hover:bg-gray-100 text-gray-900 font-extrabold rounded-2xl text-sm transition-all flex items-center justify-center gap-3 shadow-xl cursor-pointer"
            >
              <div className="grid grid-cols-2 gap-0.5 shrink-0 w-4 h-4">
                <div className="bg-[#f25f22] w-1.5 h-1.5" />
                <div className="bg-[#7fba00] w-1.5 h-1.5" />
                <div className="bg-[#00a4ef] w-1.5 h-1.5" />
                <div className="bg-[#ffb900] w-1.5 h-1.5" />
              </div>
              Sign in with Microsoft ID
            </button>

            {/* Developer Bypass (Visible ONLY on localhost and VITE_ENABLE_LOCAL_MOCK_AUTH=true) */}
            {ENABLE_LOCAL_MOCK && (
              <button 
                onClick={() => {
                  setLocalMockUser({
                    email: "alden@lotslotsmore.com",
                    displayName: "Alden Bronkhorst (Local Mock)",
                    roles: ["AIPlatform.Admin", "AIPlatform.User", "AIPlatform.Developer", "AIPlatform.Auditor"]
                  });
                  setLocalMockAuthenticated(true);
                }}
                className="w-full py-3 bg-gray-900/40 hover:bg-gray-800/40 border border-gray-800/40 text-gray-300 font-bold rounded-2xl text-sm transition-all flex items-center justify-center gap-2 cursor-pointer"
              >
                <User className="w-4 h-4 text-indigo-400" />
                Local Mock Sign In
              </button>
            )}
          </div>

          {/* Show/Hide Diagnostics link (only visible on localhost) */}
          {ENABLE_LOCAL_MOCK && (
            <button 
              onClick={() => setShowDiagnostics(!showDiagnostics)}
              className="text-[11px] text-gray-500 hover:text-indigo-400 underline cursor-pointer select-none block mx-auto"
            >
              {showDiagnostics ? "Hide Security Diagnostics" : "Show Security Diagnostics"}
            </button>
          )}

          {/* Local Diagnostics */}
          {showDiagnostics && ENABLE_LOCAL_MOCK && (
            <div className="border border-[#1e293b]/50 p-4 bg-gray-950/50 rounded-2xl text-left font-mono text-[10px] text-gray-400 space-y-1 select-text">
              <p className="text-gray-500 font-bold border-b border-[#1e293b]/30 pb-1 mb-1.5 flex items-center gap-1.5"><Shield className="w-3.5 h-3.5" /> Security Diagnostics</p>
              <p><span className="text-gray-500">inProgress:</span> {inProgress}</p>
              <p><span className="text-gray-500">accounts.length:</span> {accounts.length}</p>
              <p><span className="text-gray-500">startupAuthError:</span> {startupAuthError || "None"}</p>
              <p><span className="text-gray-500">lastError:</span> {authError || "None"}</p>
              <p><span className="text-gray-500">scopes:</span> {JSON.stringify(loginRequest.scopes)}</p>
              <p><span className="text-gray-500">currentOrigin:</span> {typeof window !== "undefined" ? window.location.origin : ""}</p>
            </div>
          )}

          <div className="border-t border-gray-800/40 pt-4 flex items-center justify-between text-xs text-gray-500 select-none">
            <span>Microsoft Security Active</span>
            <span>v1.0.0</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-[#050811] text-[#f3f4f6] font-sans antialiased overflow-hidden">
      
      {/* SIDEBAR NAVIGATION */}
      <aside className="w-64 bg-[#0a0f1d]/90 border-r border-[#1e293b]/80 flex flex-col justify-between select-none shrink-0 relative z-30">
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Logo & Branding */}
          <div className="p-6 border-b border-[#1e293b]/50 flex items-center gap-3">
            <div className="p-2 bg-indigo-600/20 border border-indigo-500/30 rounded-xl">
              <Bot className="w-6 h-6 text-indigo-400" />
            </div>
            <div>
              <h1 className="font-extrabold text-base leading-tight tracking-wide text-white">AI Platform</h1>
              <span className="text-[10px] text-indigo-400 font-extrabold tracking-widest uppercase">Assistant</span>
            </div>
          </div>

          {/* Navigation Links */}
          <nav className="p-4 space-y-1 flex-1 overflow-y-auto">
            <span className="px-4 py-2 block text-[10px] font-bold text-gray-500 uppercase tracking-widest">Business</span>
            
            <button 
              onClick={() => setActiveTab("chat")}
              className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-xs font-semibold transition-all ${activeTab === "chat" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/10 hover:text-gray-200 border border-transparent"}`}
            >
              <MessageSquare className="w-4 h-4" />
              Chat Assistant
            </button>

            <button 
              onClick={() => {
                setSelectedWorkflow(null);
                setWorkflowOutcome(null);
                setWorkflowInputs({});
                setActiveTab("workflows");
              }}
              className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-xs font-semibold transition-all ${activeTab === "workflows" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/10 hover:text-gray-200 border border-transparent"}`}
            >
              <Layers className="w-4 h-4" />
              Workflows
            </button>

            <button 
              onClick={() => setActiveTab("tasks")}
              className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-xs font-semibold transition-all ${activeTab === "tasks" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/10 hover:text-gray-200 border border-transparent"}`}
            >
              <ClipboardList className="w-4 h-4" />
              Tasks Tracker
            </button>

            <button 
              onClick={() => setActiveTab("artifacts")}
              className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-xs font-semibold transition-all ${activeTab === "artifacts" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/10 hover:text-gray-200 border border-transparent"}`}
            >
              <FileText className="w-4 h-4" />
              Documents Vault
            </button>

            <button 
              onClick={() => setActiveTab("connected-accounts")}
              className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-xs font-semibold transition-all ${activeTab === "connected-accounts" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/10 hover:text-gray-200 border border-transparent"}`}
            >
              <Database className="w-4 h-4" />
              Connected Accounts
            </button>

            {/* Gated Administrator views */}
            {hasRole(["AIPlatform.Admin", "AIPlatform.Developer", "AIPlatform.Auditor"]) && (
              <>
                <span className="px-4 py-2 pt-4 block text-[10px] font-bold text-gray-500 uppercase tracking-widest">Platform Admin</span>
                
                {hasRole(["AIPlatform.Admin", "AIPlatform.Auditor"]) && (
                  <button 
                    onClick={() => setActiveTab("audit")}
                    className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-xs font-semibold transition-all ${activeTab === "audit" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/10 hover:text-gray-200 border border-transparent"}`}
                  >
                    <ShieldAlert className="w-4 h-4" />
                    Audit Logs
                  </button>
                )}

                <button 
                  onClick={() => setActiveTab("settings")}
                  className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-xs font-semibold transition-all ${activeTab === "settings" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/10 hover:text-gray-200 border border-transparent"}`}
                >
                  <Settings className="w-4 h-4" />
                  System Settings
                </button>
              </>
            )}
          </nav>
        </div>

        {/* Profile popover section */}
        <div className="p-4 border-t border-[#1e293b]/50 relative z-40 bg-[#0a0f1d]">
          {/* Floating popover */}
          {isProfileMenuOpen && (
            <div className="absolute bottom-16 left-4 right-4 bg-[#0a0f1d] border border-[#1e293b] rounded-2xl shadow-2xl p-2 py-3 space-y-1.5 z-50 animate-fade-in text-left">
              <div className="px-3 py-1">
                <p className="text-xs font-bold text-white truncate">{activeUser.displayName}</p>
                <p className="text-[10px] text-gray-500 truncate mt-0.5">{activeUser.email}</p>
              </div>
              <div className="border-t border-[#1e293b]/50 my-1" />
              
              {hasRole(["AIPlatform.Admin", "AIPlatform.Developer"]) && (
                <button 
                  onClick={() => {
                    setActiveTab("settings");
                    setIsProfileMenuOpen(false);
                  }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-gray-400 hover:text-white hover:bg-gray-800/40 rounded-xl text-left transition-all cursor-pointer"
                >
                  <Settings className="w-3.5 h-3.5 text-indigo-400" />
                  System Settings
                </button>
              )}
              
              <button 
                onClick={() => {
                  setIsProfileMenuOpen(false);
                  handleSignOut();
                }}
                className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 rounded-xl text-left transition-all cursor-pointer"
              >
                <LogOut className="w-3.5 h-3.5 text-rose-400" />
                Sign Out
              </button>
            </div>
          )}

          {/* Profile trigger button */}
          <button 
            onClick={() => setIsProfileMenuOpen(!isProfileMenuOpen)}
            className="w-full flex items-center justify-between p-2 rounded-xl bg-gray-800/10 hover:bg-gray-800/20 border border-gray-800/40 transition-all cursor-pointer"
          >
            <div className="flex items-center gap-2.5 overflow-hidden">
              <div className="w-8 h-8 rounded-lg bg-indigo-600/15 border border-indigo-500/25 flex items-center justify-center shrink-0">
                <User className="w-4 h-4 text-indigo-400" />
              </div>
              <div className="text-left overflow-hidden">
                <p className="text-xs font-bold text-white truncate">{activeUser.displayName}</p>
                <span className="text-[9px] text-gray-500 truncate block">Microsoft ID Active</span>
              </div>
            </div>
            <ChevronDown className={`w-3.5 h-3.5 text-gray-500 transition-all ${isProfileMenuOpen ? "rotate-180" : ""}`} />
          </button>
        </div>
      </aside>

      {/* CHAT SESSION DRAWER */}
      {activeTab === "chat" && (
        <aside className="w-64 bg-[#080c17]/90 border-r border-[#1e293b]/40 flex flex-col justify-between shrink-0 select-none relative z-20">
          <div className="p-4 border-b border-[#1e293b]/30">
            <button 
              onClick={() => createNewChat()}
              className="w-full py-2 bg-indigo-600/10 hover:bg-indigo-600/25 border border-indigo-500/30 text-indigo-400 rounded-xl text-xs font-bold tracking-wide transition-all flex items-center justify-center gap-1.5 cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              New Chat
            </button>
          </div>

          {/* Sessions List */}
          <div className="flex-1 overflow-y-auto p-3 space-y-1">
            <span className="px-3 py-1.5 block text-[10px] font-bold text-gray-500 uppercase tracking-widest">Recent Chats</span>
            {isSessionsLoading ? (
              <div className="text-center py-6 text-xs text-gray-500">Loading chats...</div>
            ) : (
              chatSessions.map(sess => (
                <div 
                  key={sess.id}
                  onClick={() => setActiveSession(sess)}
                  className={`group p-2.5 rounded-xl cursor-pointer transition-all flex items-center justify-between border ${
                    activeSession?.id === sess.id 
                      ? "bg-indigo-600/10 border-indigo-500/25 text-white" 
                      : "border-transparent text-gray-400 hover:bg-gray-800/10 hover:text-gray-200"
                  }`}
                >
                  <div className="overflow-hidden flex-1 pr-2">
                    <p className="text-xs font-semibold truncate leading-tight">{sess.title}</p>
                    {sess.workflow_context && (
                      <span className="text-[9px] text-indigo-400 font-bold block truncate mt-0.5 uppercase tracking-wide">
                        Context: {sess.workflow_context.split("_").join(" ")}
                      </span>
                    )}
                  </div>
                  
                  <button 
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteChatSession(sess.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-rose-400 p-1 rounded hover:bg-rose-500/10 transition-all shrink-0"
                    title="Archive/Delete Chat"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))
            )}
          </div>
        </aside>
      )}

      {/* MAIN CONTAINER */}
      <main className="flex-1 flex flex-col overflow-hidden">
        
        {/* HEADER */}
        <header className="h-16 bg-[#0a0f1d] border-b border-[#1e293b]/50 px-8 flex justify-between items-center select-none shrink-0 relative z-10">
          <div className="flex items-center gap-3">
            <span className="text-xs uppercase tracking-widest text-indigo-400 font-bold">{activeTab}</span>
            <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse" />
            
            {/* Context aware chat banner */}
            {activeTab === "chat" && activeSession?.workflow_context && (
              <span className="ml-4 flex items-center gap-1.5 px-3 py-1 bg-indigo-500/15 border border-indigo-500/20 text-indigo-300 rounded-full text-xs font-semibold">
                <Compass className="w-3.5 h-3.5" />
                Active Context: {BUSINESS_WORKFLOWS.find(w=>w.id===activeSession.workflow_context)?.title}
              </span>
            )}
          </div>

          <div className="flex items-center gap-4">
            {/* Minimal un-cluttered header, top-right is empty as profile menu is in bottom-left popover */}
          </div>
        </header>

        {/* COMPONENT VIEWS */}
        <section className="flex-1 overflow-y-auto p-8 bg-[#070b15] relative z-0">
          
          {/* CHAT ASSISTANT VIEW */}
          {activeTab === "chat" && activeSession && (
            <div className="h-full flex flex-col justify-between max-w-4xl mx-auto liquid-glass rounded-3xl overflow-hidden shadow-2xl">
              
              {/* Message Flow */}
              <div className="flex-1 overflow-y-auto p-6 space-y-6">
                {isMessagesLoading ? (
                  <div className="text-center py-20 text-gray-400">Retrieving chat messages...</div>
                ) : chatMessages.length === 0 ? (
                  <div className="text-center py-20 text-gray-500 select-none space-y-2">
                    <Bot className="w-10 h-10 text-gray-700 mx-auto mb-2" />
                    <p className="font-semibold">This conversation has no messages yet.</p>
                    <p className="text-xs text-gray-600 max-w-xs mx-auto">Ask the AI Platform assistant to audit credit notes, check attendance exceptions, or examine Odoo accounts!</p>
                  </div>
                ) : (
                  chatMessages.map((msg) => (
                    <div key={msg.id} className={`flex gap-4 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                      
                      {msg.role === "assistant" && (
                        <div className="w-8 h-8 rounded-lg bg-indigo-600/15 border border-indigo-500/25 flex items-center justify-center shrink-0">
                          <Bot className="w-4 h-4 text-indigo-400" />
                        </div>
                      )}

                      <div className={`max-w-[75%] p-4 rounded-2xl border text-xs leading-relaxed whitespace-pre-wrap ${
                        msg.role === "user" 
                          ? "bg-indigo-600/10 border-indigo-500/25 text-indigo-50 rounded-tr-none" 
                          : "bg-gray-800/20 border-gray-800/50 text-gray-200 rounded-tl-none"
                      }`}>
                        {msg.content}

                        {/* Collapsible Technical Details (View details option) */}
                        {msg.metadata_json?.technical_details && (
                          <div className="mt-3 pt-3 border-t border-gray-800/40">
                            <button 
                              onClick={() => setExpandedTraceMsgs(prev => ({ ...prev, [msg.id]: !prev[msg.id] }))}
                              className="text-[10px] text-gray-500 hover:text-indigo-400 font-semibold flex items-center gap-1 cursor-pointer select-none"
                            >
                              <Shield className="w-3 h-3" />
                              {expandedTraceMsgs[msg.id] ? "Hide technical trail" : "View operational trail"}
                            </button>

                            {expandedTraceMsgs[msg.id] && (
                              <pre className="mt-2.5 p-3 bg-gray-950/50 border border-[#1e293b]/50 rounded-xl overflow-x-auto text-[10px] font-mono text-gray-400 max-h-48 overflow-y-auto">
                                {JSON.stringify(msg.metadata_json.technical_details, null, 2)}
                              </pre>
                            )}
                          </div>
                        )}
                      </div>

                      {msg.role === "user" && (
                        <div className="w-8 h-8 rounded-lg bg-gray-800/30 border border-gray-800 flex items-center justify-center shrink-0">
                          <User className="w-4 h-4 text-gray-300" />
                        </div>
                      )}

                    </div>
                  ))
                )}
              </div>

              {/* Chat Input / Composer */}
              <div className="p-4 border-t border-[#1e293b]/50 bg-[#0a0f1d]/90 flex flex-col gap-3 select-none">
                
                {/* File chips attachment container */}
                {attachedFiles.length > 0 && (
                  <div className="flex flex-wrap gap-2 pb-1.5 border-b border-gray-800/30">
                    {attachedFiles.map((chip, idx) => (
                      <div key={idx} className="flex items-center gap-1.5 px-3 py-1 bg-indigo-600/10 border border-indigo-500/25 rounded-full text-xs text-indigo-300 font-semibold animate-fade-in">
                        <FileText className="w-3.5 h-3.5 shrink-0" />
                        <span className="truncate max-w-[120px]">{chip.file.name}</span>
                        {chip.uploading ? (
                          <RefreshCw className="w-3 h-3 animate-spin shrink-0 ml-1 text-indigo-400" />
                        ) : (
                          <button 
                            type="button"
                            onClick={() => chip.id && handleRemoveFile(chip.id)}
                            className="text-indigo-400 hover:text-indigo-200 ml-1 text-xs shrink-0 cursor-pointer"
                          >
                            ✕
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                <form onSubmit={handleSendMessage} className="flex gap-3 items-center">
                  <input 
                    type="file"
                    ref={fileInputRef}
                    onChange={handleFileUpload}
                    className="hidden"
                    multiple
                  />
                  <button 
                    type="button"
                    onClick={handleTriggerUpload}
                    className="p-3 bg-gray-800/20 border border-gray-800/60 text-gray-400 hover:text-white rounded-xl hover:bg-gray-800/40 transition-all cursor-pointer"
                    title="Upload secure business documents"
                  >
                    <Paperclip className="w-4 h-4" />
                  </button>

                  <input 
                    type="text"
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    placeholder={activeSession.workflow_context ? `Ask Odoo about ${BUSINESS_WORKFLOWS.find(w=>w.id===activeSession.workflow_context)?.title}...` : "Ask AI Assistant anything..."}
                    disabled={isChatSending}
                    className="flex-1 px-4 py-3 bg-[#070b15] border border-[#1e293b] rounded-xl focus:outline-none focus:border-indigo-500 text-xs placeholder-gray-500 text-white"
                  />

                  {/* Microphone Button */}
                  <button 
                    type="button"
                    onClick={handleToggleVoice}
                    className={`p-3 border rounded-xl transition-all cursor-pointer relative ${
                      voiceState === "listening" 
                        ? "bg-rose-500/15 border-rose-500 text-rose-400 animate-pulse" 
                        : "bg-gray-800/20 border-gray-800/60 text-gray-400 hover:text-white hover:bg-gray-800/40"
                    }`}
                    title={voiceState === "unsupported" ? "Voice input unsupported" : "Speak voice message"}
                  >
                    {voiceState === "listening" ? <Mic className="w-4 h-4" /> : <MicOff className="w-4 h-4" />}
                  </button>

                  <button 
                    type="submit"
                    disabled={isChatSending || (!chatInput.trim() && attachedFiles.length === 0)}
                    className="p-3 px-5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white rounded-xl text-xs font-bold transition-all flex items-center gap-1.5 cursor-pointer"
                  >
                    Send
                    <CornerDownLeft className="w-3.5 h-3.5" />
                  </button>
                </form>

                <div className="flex items-center justify-between px-2 text-[10px] text-gray-500 select-none">
                  {voiceState === "listening" ? (
                    <span className="text-rose-400 font-semibold flex items-center gap-1 animate-pulse"><Mic className="w-3.5 h-3.5" /> Speak now... browser transcriber listening</span>
                  ) : voiceState === "processing" ? (
                    <span className="text-indigo-400 font-semibold flex items-center gap-1"><RefreshCw className="w-3.5 h-3.5 animate-spin" /> Processing voice transcript...</span>
                  ) : voiceState === "denied" ? (
                    <span className="text-rose-400 font-semibold flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" /> Mic permission denied</span>
                  ) : (
                    <span>Supports voice recording & multi-file uploads securely</span>
                  )}
                  <span>All documents are encrypted in Microsoft Azure Storage</span>
                </div>
              </div>
            </div>
          )}

          {/* NO ACTIVE CHAT STATE */}
          {activeTab === "chat" && !activeSession && (
            <div className="p-8 border border-[#1e293b]/50 border-dashed rounded-3xl bg-transparent text-center py-16 text-gray-400 select-none max-w-lg mx-auto mt-20">
              <MessageSquare className="w-10 h-10 text-gray-600 mb-3 mx-auto animate-bounce" />
              <p className="font-semibold text-gray-300">No active chat session selected</p>
              <p className="text-xs text-gray-500 max-w-sm mx-auto mt-1">To begin, click \"New Chat\" in the chat drawer or select an active operational workflow!</p>
            </div>
          )}

          {/* WORKFLOWS DECK VIEW */}
          {activeTab === "workflows" && !selectedWorkflow && (
            <div className="max-w-6xl mx-auto space-y-8 select-none">
              <div className="p-8 border border-[#1e293b] rounded-2xl bg-gradient-to-r from-indigo-900/10 to-transparent flex items-center justify-between">
                <div>
                  <h2 className="text-xl font-bold text-white mb-2">Automated Business Workflows</h2>
                  <p className="text-sm text-gray-400 max-w-2xl">Execute structured operational tasks on behalf of your connected accounts. These workflows automate standard ledger checks, cross-verifications, and report compilation.</p>
                </div>
                <BookOpen className="w-12 h-12 text-indigo-500/25 shrink-0" />
              </div>

              {/* Grouped Workflow categories */}
              {["finance", "hr", "operations"].map((cat) => (
                <div key={cat} className="space-y-4">
                  <h3 className="text-xs uppercase tracking-widest text-gray-500 font-bold flex items-center gap-2">
                    {cat === "finance" ? <DollarSign className="w-4 h-4 text-emerald-400" /> : cat === "hr" ? <Users className="w-4 h-4 text-sky-400" /> : <Layers className="w-4 h-4 text-amber-400" />}
                    {cat === "finance" ? "Finance Ledger Operations" : cat === "hr" ? "HR & Timesheet Management" : "Backlog & Operations"}
                  </h3>
                  
                  <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {BUSINESS_WORKFLOWS
                      .filter(card => card.category === cat)
                      .map((workflow) => (
                        <div 
                          key={workflow.id}
                          onClick={() => {
                            setSelectedWorkflow(workflow);
                            setWorkflowInputs({});
                            setWorkflowOutcome(null);
                          }}
                          className="p-6 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] hover:border-indigo-500/50 hover:bg-gray-800/10 transition-all cursor-pointer flex flex-col justify-between"
                        >
                          <div>
                            <h4 className="font-bold text-sm text-white mb-2">{workflow.title}</h4>
                            <p className="text-xs text-gray-400 leading-relaxed">{workflow.description}</p>
                          </div>
                          <div className="mt-5 flex items-center gap-1.5 text-xs text-indigo-400 font-semibold hover:text-indigo-300">
                            Configure Guided Screen <ArrowRight className="w-3.5 h-3.5" />
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* GUIDED WORKFLOW SCREEN */}
          {activeTab === "workflows" && selectedWorkflow && (
            <div className="max-w-2xl mx-auto space-y-6">
              
              <button 
                onClick={() => {
                  setSelectedWorkflow(null);
                  setWorkflowOutcome(null);
                }}
                className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-white cursor-pointer select-none"
              >
                <ArrowLeft className="w-4 h-4" /> Back to Business Workflows
              </button>

              <div className="p-6 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] space-y-6">
                <div>
                  <h2 className="text-lg font-bold text-white mb-1.5">{selectedWorkflow.title}</h2>
                  <p className="text-xs text-gray-400 leading-relaxed">{selectedWorkflow.description}</p>
                </div>

                <div className="space-y-4">
                  {selectedWorkflow.inputs.map((input) => (
                    <div key={input.name}>
                      <label className="text-xs text-gray-400 font-bold block mb-1.5 uppercase">{input.label}</label>
                      {input.type === "select" ? (
                        <select 
                          value={workflowInputs[input.name] || ""}
                          onChange={(e) => setWorkflowInputs(prev => ({ ...prev, [input.name]: e.target.value }))}
                          className="w-full px-4 py-3 bg-[#070b15] border border-[#1e293b] rounded-xl focus:outline-none focus:border-indigo-500 text-sm text-white"
                        >
                          <option value="">Choose Options...</option>
                          {input.options?.map(opt => (
                            <option key={opt} value={opt}>{opt}</option>
                          ))}
                        </select>
                      ) : (
                        <input 
                          type={input.type}
                          value={workflowInputs[input.name] || ""}
                          onChange={(e) => setWorkflowInputs(prev => ({ ...prev, [input.name]: e.target.value }))}
                          placeholder={input.placeholder}
                          className="w-full px-4 py-3 bg-[#070b15] border border-[#1e293b] rounded-xl focus:outline-none focus:border-indigo-500 text-sm text-white"
                        />
                      )}
                    </div>
                  ))}
                </div>

                <div className="flex gap-4 pt-4 border-t border-[#1e293b]/50">
                  <button 
                    onClick={() => handleLaunchContextualChat(selectedWorkflow)}
                    className="flex-1 py-3 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-sm font-semibold tracking-wide transition-all flex items-center justify-center gap-2 cursor-pointer"
                  >
                    <Bot className="w-4 h-4 text-indigo-400" />
                    Ask AI Assistant
                  </button>

                  <button 
                    onClick={handleRunWorkflow}
                    disabled={isWorkflowRunning}
                    className="flex-1 py-3 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white rounded-xl text-sm font-bold tracking-wide transition-all flex items-center justify-center gap-2 cursor-pointer"
                  >
                    {isWorkflowRunning ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                    {isWorkflowRunning ? "Executing..." : "Execute Workflow"}
                  </button>
                </div>
              </div>

              {workflowOutcome && (
                <div className={`p-5 border rounded-2xl flex items-start gap-4 text-sm ${workflowOutcome.success ? "bg-emerald-500/10 border-emerald-500/25 text-emerald-400" : "bg-rose-500/10 border-rose-500/25 text-rose-400"}`}>
                  {workflowOutcome.success ? <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" /> : <XCircle className="w-5 h-5 shrink-0 mt-0.5" />}
                  <div className="space-y-2 flex-1">
                    <p className="font-semibold text-white">{workflowOutcome.success ? "Execution Completed Successfully" : "Execution Failed"}</p>
                    <p className="opacity-90">{workflowOutcome.message}</p>
                    
                    {workflowOutcome.success && (
                      <div className="mt-3 p-3 bg-gray-950/40 border border-[#1e293b] rounded-xl text-xs space-y-1.5 font-mono select-text text-gray-300">
                        <p><span className="text-gray-500">Platform Job Reference:</span> {workflowOutcome.jobId || "None Created"}</p>
                        <p><span className="text-gray-500">Secure Document Artifact:</span> {workflowOutcome.artifactId || "None Generated"}</p>
                      </div>
                    )}
                  </div>
                </div>
              )}

            </div>
          )}

          {/* TASKS VIEW */}
          {activeTab === "tasks" && (
            <div className="max-w-6xl mx-auto space-y-6">
              <div className="flex justify-between items-center select-none">
                <div>
                  <h2 className="text-xl font-bold text-white">Tasks Tracker</h2>
                  <p className="text-sm text-gray-400 mt-1">Check Odoo operational tasks, biometric anomalies, and assigned backlogs.</p>
                </div>
                <button 
                  onClick={fetchJobs} 
                  disabled={isJobsLoading}
                  className="p-2 bg-gray-800 hover:bg-gray-700 rounded-xl transition-all cursor-pointer"
                >
                  <RefreshCw className={`w-4 h-4 text-white ${isJobsLoading ? "animate-spin" : ""}`} />
                </button>
              </div>

              {isJobsLoading ? (
                <div className="text-center py-20 text-gray-400">Loading tasks...</div>
              ) : jobs.length === 0 ? (
                <div className="p-8 border border-[#1e293b]/50 border-dashed rounded-2xl bg-transparent text-center py-16 text-gray-400 select-none">
                  <ClipboardList className="w-10 h-10 text-gray-600 mb-3 mx-auto" />
                  <p className="font-semibold text-gray-300">No active tasks found</p>
                  <p className="text-xs text-gray-500 max-w-sm mx-auto mt-1">Biometric clock-in exception audits and claim mismatches appear as tasks here.</p>
                </div>
              ) : (
                <div className="grid gap-4 select-text">
                  {jobs.map((job) => (
                    <div key={job.id} className="p-5 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] flex items-center justify-between">
                      <div className="flex items-center gap-4">
                        <div className="w-10 h-10 rounded-xl bg-gray-800 border border-[#1e293b] flex items-center justify-center text-gray-400">
                          <ClipboardList className="w-5 h-5" />
                        </div>
                        <div>
                          <h4 className="font-semibold text-white text-sm">{job.title}</h4>
                          <div className="flex gap-2 items-center mt-1.5 text-[11px] font-mono text-gray-500">
                            <span>ID: {job.id.slice(0, 8)}...</span>
                            <span>•</span>
                            <span>Created: {new Date(job.created_at).toLocaleDateString()}</span>
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-3">
                        <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase ${
                          job.status === "completed" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                        }`}>
                          {job.status}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* DOCUMENTS VIEW */}
          {activeTab === "artifacts" && (
            <div className="max-w-6xl mx-auto space-y-6">
              <div className="flex justify-between items-center select-none">
                <div>
                  <h2 className="text-xl font-bold text-white">Documents Vault</h2>
                  <p className="text-sm text-gray-400 mt-1">Access secure Odoo final outputs, supplier statements, and compiled attendance reports.</p>
                </div>
                <button 
                  onClick={fetchArtifacts} 
                  disabled={isArtifactsLoading}
                  className="p-2 bg-gray-800 hover:bg-gray-700 rounded-xl transition-all cursor-pointer"
                >
                  <RefreshCw className={`w-4 h-4 text-white ${isArtifactsLoading ? "animate-spin" : ""}`} />
                </button>
              </div>

              {isArtifactsLoading ? (
                <div className="text-center py-20 text-gray-400">Loading documents...</div>
              ) : artifacts.length === 0 ? (
                <div className="p-8 border border-[#1e293b]/50 border-dashed rounded-2xl bg-transparent text-center py-16 text-gray-400 select-none">
                  <FileText className="w-10 h-10 text-gray-600 mb-3 mx-auto" />
                  <p className="font-semibold text-gray-300">No documents found</p>
                  <p className="text-xs text-gray-500 max-w-sm mx-auto mt-1">Executed attendance and pricing review workflows generate Excel and PDF audit summaries.</p>
                </div>
              ) : (
                <div className="grid md:grid-cols-3 gap-6 select-text">
                  {artifacts.map((art) => (
                    <div key={art.id} className="p-5 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] flex flex-col justify-between">
                      <div>
                        <div className="flex justify-between items-start mb-3">
                          <span className="bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 px-2.5 py-0.5 rounded-full text-[10px] font-mono uppercase">{art.artifact_type}</span>
                          <HardDrive className="w-4 h-4 text-gray-600" />
                        </div>
                        <h4 className="font-semibold text-white truncate text-sm" title={art.filename}>{art.filename}</h4>
                        <p className="text-xs text-gray-500 font-mono mt-1">MIME: {art.mime_type}</p>
                      </div>

                      <div className="mt-4 pt-3 border-t border-[#1e293b]/50 flex justify-between items-center text-xs">
                        <span className="text-gray-500 font-mono text-[10px]">Job: {art.job_id?.slice(0, 8)}...</span>
                        <a 
                          href={art.storage_uri} 
                          target="_blank" 
                          rel="noreferrer"
                          className="text-indigo-400 hover:text-indigo-300 font-semibold tracking-wide flex items-center gap-1 hover:underline cursor-pointer"
                        >
                          Raw URL
                          <ExternalLink className="w-3.5 h-3.5" />
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* CONNECTED ACCOUNTS VIEW */}
          {activeTab === "connected-accounts" && (
            <div className="max-w-5xl mx-auto space-y-8 select-none">
              <div className="p-8 border border-[#1e293b] rounded-2xl bg-gradient-to-r from-indigo-900/10 to-transparent flex items-center justify-between">
                <div>
                  <h2 className="text-xl font-bold text-white mb-2">Connected Accounts</h2>
                  <p className="text-sm text-gray-400 max-w-2xl">Connect third-party corporate databases and integrations. All connection credentials and API keys are stored safely inside secure Key Vault containers.</p>
                </div>
                <BookOpen className="w-12 h-12 text-indigo-500/25 shrink-0" />
              </div>

              <div className="grid md:grid-cols-2 gap-6">
                
                {/* ODOO ACCOUNT CARD */}
                <div className="p-6 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] flex flex-col justify-between">
                  <div>
                    <div className="flex items-center justify-between mb-4">
                      <div className="flex items-center gap-3">
                        <div className="p-2.5 bg-orange-600/10 border border-orange-500/35 rounded-xl">
                          <Database className="w-6 h-6 text-orange-400" />
                        </div>
                        <div>
                          <h3 className="font-bold text-white leading-tight">Odoo Enterprise</h3>
                          <span className="text-xs text-gray-500 font-mono">ERP Proxy Connector</span>
                        </div>
                      </div>

                      {isStatusLoading ? (
                        <span className="text-xs bg-gray-800 text-gray-400 px-3 py-1 rounded-full font-medium flex items-center gap-1.5"><RefreshCw className="w-3 h-3 animate-spin" /> Checking</span>
                      ) : odooStatus.status === "connected" ? (
                        <span className="text-xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/25 px-3 py-1 rounded-full font-medium flex items-center gap-1.5"><CheckCircle2 className="w-3.5 h-3.5" /> Connected</span>
                      ) : odooStatus.status === "error" ? (
                        <span className="text-xs bg-rose-500/10 text-rose-400 border border-rose-500/25 px-3 py-1 rounded-full font-medium flex items-center gap-1.5"><AlertTriangle className="w-3.5 h-3.5" /> Credentials Error</span>
                      ) : (
                        <span className="text-xs bg-gray-800/50 text-gray-400 border border-gray-800 px-3 py-1 rounded-full font-medium">Not Connected</span>
                      )}
                    </div>

                    <div className="space-y-3 py-4 border-t border-b border-[#1e293b]/50 text-sm text-gray-400 font-medium select-text">
                      <div className="flex justify-between">
                        <span>Username:</span>
                        <span className="text-white font-mono">{odooStatus.provider_username || "—"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Environment:</span>
                        <span className="text-white capitalize">{odooStatus.target_environment || "—"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Last Verified:</span>
                        <span className="text-white text-xs font-mono">{odooStatus.last_verified_at ? new Date(odooStatus.last_verified_at).toLocaleString() : "—"}</span>
                      </div>
                    </div>
                  </div>

                  <div className="mt-6 flex flex-wrap gap-3">
                    {odooStatus.status === "connected" || odooStatus.status === "error" ? (
                      <>
                        <button 
                          onClick={handleTestOdoo}
                          disabled={isTesting}
                          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-xs font-semibold tracking-wide transition-all flex items-center gap-1.5 cursor-pointer"
                        >
                          <RefreshCw className={`w-3.5 h-3.5 ${isTesting ? "animate-spin" : ""}`} />
                          {isTesting ? "Testing..." : "Test Connection"}
                        </button>
                        <button 
                          onClick={() => setIsRotateOpen(true)}
                          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-xs font-semibold tracking-wide transition-all flex items-center gap-1.5 cursor-pointer"
                        >
                          <Key className="w-3.5 h-3.5" />
                          Rotate Key
                        </button>
                        <button 
                          onClick={handleDisconnectOdoo}
                          className="px-4 py-2 bg-rose-600/10 hover:bg-rose-600/25 border border-rose-500/25 text-rose-400 rounded-xl text-xs font-semibold tracking-wide transition-all flex items-center gap-1.5 cursor-pointer"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                          Disconnect
                        </button>
                      </>
                    ) : (
                      <button 
                        onClick={() => setIsConnectOpen(true)}
                        className="w-full py-3 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-sm font-bold tracking-wide transition-all flex items-center justify-center gap-2 cursor-pointer"
                      >
                        <Plus className="w-4 h-4" />
                        Connect Odoo Account
                      </button>
                    )}
                  </div>
                </div>

                <div className="p-6 border border-[#1e293b]/50 border-dashed rounded-2xl bg-transparent flex flex-col justify-center items-center text-center p-8 select-none">
                  <Database className="w-8 h-8 text-gray-600 mb-3" />
                  <h4 className="font-bold text-gray-400 mb-1">Microsoft / Microsoft 365</h4>
                  <p className="text-xs text-gray-500 max-w-xs">Connecting SharePoint, Outlook and Microsoft Graph is deferred to next platform iteration.</p>
                </div>

              </div>

              {testResult && (
                <div className={`p-4 border rounded-xl flex items-start gap-3 text-sm ${testResult.success ? "bg-emerald-500/10 border-emerald-500/25 text-emerald-400" : "bg-rose-500/10 border-rose-500/25 text-rose-400"}`}>
                  {testResult.success ? <CheckCircle2 className="w-5 h-5 shrink-0" /> : <XCircle className="w-5 h-5 shrink-0" />}
                  <div>
                    <p className="font-semibold">{testResult.success ? "Verification Success" : "Verification Failed"}</p>
                    <p className="mt-0.5">{testResult.message}</p>
                  </div>
                </div>
              )}
              {/* Odoo Connect Modal Overlay */}
              {isConnectOpen && (
                <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
                  <div className="bg-[#0a0f1d] border border-[#1e293b] rounded-2xl max-w-lg w-full overflow-hidden shadow-2xl">
                    <div className="p-6 border-b border-[#1e293b] flex justify-between items-center select-none">
                      <h3 className="font-bold text-lg text-white">Connect Odoo Enterprise</h3>
                      <button onClick={() => setIsConnectOpen(false)} className="text-gray-400 hover:text-white">✕</button>
                    </div>
                    <form onSubmit={handleConnectOdoo} className="p-6 space-y-4 text-left">
                      <div>
                        <label className="text-xs text-gray-400 font-bold block mb-1.5 uppercase">Odoo Instance URL</label>
                        <input 
                          type="url" 
                          required 
                          value={odooUrl}
                          onChange={(e) => setOdooUrl(e.target.value)}
                          className="w-full px-4 py-3 bg-[#070b15] border border-[#1e293b] rounded-xl focus:outline-none focus:border-indigo-500 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-400 font-bold block mb-1.5 uppercase">Odoo Database Name</label>
                        <input 
                          type="text" 
                          required 
                          value={odooDb}
                          onChange={(e) => setOdooDb(e.target.value)}
                          className="w-full px-4 py-3 bg-[#070b15] border border-[#1e293b] rounded-xl focus:outline-none focus:border-indigo-500 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-400 font-bold block mb-1.5 uppercase">Odoo Username / Email</label>
                        <input 
                          type="email" 
                          required 
                          value={odooUsername}
                          onChange={(e) => setOdooUsername(e.target.value)}
                          className="w-full px-4 py-3 bg-[#070b15] border border-[#1e293b] rounded-xl focus:outline-none focus:border-indigo-500 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-400 font-bold block mb-1.5 uppercase">Odoo API Key / Password</label>
                        <input 
                          type="password" 
                          required 
                          value={odooApiKey}
                          onChange={(e) => setOdooApiKey(e.target.value)}
                          placeholder="Input Odoo API Key securely..."
                          className="w-full px-4 py-3 bg-[#070b15] border border-[#1e293b] rounded-xl focus:outline-none focus:border-indigo-500 text-sm placeholder-gray-600"
                        />
                      </div>

                      <div className="pt-4 flex gap-3">
                        <button 
                          type="button" 
                          onClick={() => setIsConnectOpen(false)}
                          className="flex-1 py-3 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-sm font-semibold tracking-wide transition-all cursor-pointer"
                        >
                          Cancel
                        </button>
                        <button 
                          type="submit"
                          disabled={isConnecting}
                          className="flex-1 py-3 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white rounded-xl text-sm font-bold tracking-wide transition-all cursor-pointer"
                        >
                          {isConnecting ? "Connecting..." : "Verify & Save"}
                        </button>
                      </div>
                    </form>
                  </div>
                </div>
              )}

              {/* Odoo Rotate Key Modal Overlay */}
              {isRotateOpen && (
                <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
                  <div className="bg-[#0a0f1d] border border-[#1e293b] rounded-2xl max-w-md w-full overflow-hidden shadow-2xl">
                    <div className="p-6 border-b border-[#1e293b] flex justify-between items-center select-none">
                      <h3 className="font-bold text-lg text-white">Rotate API Key</h3>
                      <button onClick={() => setIsRotateOpen(false)} className="text-gray-400 hover:text-white">✕</button>
                    </div>
                    <form onSubmit={handleRotateOdoo} className="p-6 space-y-4 text-left">
                      <div>
                        <label className="text-xs text-gray-400 font-bold block mb-1.5 uppercase">New Odoo API Key / Password</label>
                        <input 
                          type="password" 
                          required 
                          value={odooApiKey}
                          onChange={(e) => setOdooApiKey(e.target.value)}
                          placeholder="Input new API Key securely..."
                          className="w-full px-4 py-3 bg-[#070b15] border border-[#1e293b] rounded-xl focus:outline-none focus:border-indigo-500 text-sm placeholder-gray-600"
                        />
                      </div>

                      <div className="pt-4 flex gap-3">
                        <button 
                          type="button" 
                          onClick={() => setIsRotateOpen(false)}
                          className="flex-1 py-3 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-sm font-semibold tracking-wide transition-all cursor-pointer"
                        >
                          Cancel
                        </button>
                        <button 
                          type="submit"
                          disabled={isConnecting}
                          className="flex-1 py-3 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white rounded-xl text-sm font-bold tracking-wide transition-all cursor-pointer"
                        >
                          {isConnecting ? "Updating..." : "Rotate Key"}
                        </button>
                      </div>
                    </form>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* AUDIT LOG TAB */}
          {activeTab === "audit" && hasRole(["AIPlatform.Admin", "AIPlatform.Auditor"]) && (
            <div className="max-w-6xl mx-auto space-y-6">
              <div className="flex justify-between items-center select-none">
                <div>
                  <h2 className="text-xl font-bold text-white">Audit Log Viewer</h2>
                  <p className="text-sm text-gray-400 mt-1">Comprehensive log of third-party proxy requests, target models, and risk levels.</p>
                </div>
                
                <div className="flex items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 text-gray-500 absolute left-3 top-2.5" />
                    <input 
                      type="text"
                      placeholder="Filter logs..."
                      value={auditFilter}
                      onChange={(e) => setAuditFilter(e.target.value)}
                      className="pl-9 pr-4 py-2 bg-gray-800/35 border border-[#1e293b] rounded-lg text-xs placeholder-gray-500 focus:outline-none focus:border-indigo-500 w-48 text-white"
                    />
                  </div>
                  <button 
                    onClick={fetchAuditLogs} 
                    disabled={isAuditLoading}
                    className="p-2 bg-gray-800 hover:bg-gray-700 rounded-xl transition-all cursor-pointer"
                  >
                    <RefreshCw className={`w-4 h-4 text-white ${isAuditLoading ? "animate-spin" : ""}`} />
                  </button>
                </div>
              </div>

              {isAuditLoading ? (
                <div className="text-center py-20 text-gray-400 select-none">Loading logs...</div>
              ) : auditLogs.length === 0 ? (
                <div className="p-8 border border-[#1e293b]/50 border-dashed rounded-2xl bg-transparent text-center py-16 text-gray-400 select-none">
                  <Shield className="w-10 h-10 text-gray-600 mb-3 mx-auto" />
                  <p className="font-semibold text-gray-300">No audit events generated</p>
                  <p className="text-xs text-gray-500 max-w-sm mx-auto mt-1">Audit events are captured automatically for Odoo connections and proxy endpoints.</p>
                </div>
              ) : (
                <div className="grid lg:grid-cols-3 gap-6 items-start">
                  
                  {/* Table View */}
                  <div className="lg:col-span-2 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] overflow-hidden select-text">
                    <table className="w-full text-left border-collapse text-xs">
                      <thead>
                        <tr className="bg-gray-800/30 border-b border-[#1e293b] text-gray-400 font-bold uppercase tracking-wider select-none">
                          <th className="p-3">Action</th>
                          <th className="p-3">Model</th>
                          <th className="p-3">Status</th>
                          <th className="p-3">Risk</th>
                          <th className="p-3">Time</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[#1e293b]/50">
                        {auditLogs
                          .filter(log => !auditFilter || log.action_type.includes(auditFilter) || (log.target_model && log.target_model.includes(auditFilter)))
                          .map((log) => (
                            <tr 
                              key={log.id} 
                              onClick={() => setInspectLog(log)}
                              className={`cursor-pointer hover:bg-gray-800/25 transition-all ${inspectLog?.id === log.id ? "bg-indigo-600/10" : ""}`}
                            >
                              <td className="p-3 font-semibold text-indigo-400 uppercase font-mono">{log.action_type}</td>
                              <td className="p-3 font-mono text-gray-300">{log.target_model || "—"}</td>
                              <td className="p-3">
                                <span className={`inline-flex px-2 py-0.5 rounded text-[10px] font-bold ${
                                  log.status === "success" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                                }`}>
                                  {log.status}
                                </span>
                              </td>
                              <td className="p-3">
                                <span className="text-gray-400 font-mono capitalize">{log.risk_level}</span>
                              </td>
                              <td className="p-3 text-gray-500 font-mono">{new Date(log.timestamp).toLocaleTimeString()}</td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>

                  {/* Inspector Panel */}
                  <div className="border border-[#1e293b] rounded-2xl bg-[#0a0f1d] p-5 space-y-4 select-text">
                    <div className="flex justify-between items-center select-none border-b border-[#1e293b] pb-3">
                      <h3 className="font-bold text-sm text-white">Event Inspector</h3>
                      <span className="text-xs text-gray-500 font-mono">Detail View</span>
                    </div>

                    {inspectLog ? (
                      <div className="space-y-4 text-xs font-medium">
                        <div className="grid grid-cols-3 gap-2">
                          <span className="text-gray-500">Action:</span>
                          <span className="col-span-2 text-white font-mono uppercase">{inspectLog.action_type}</span>
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          <span className="text-gray-500">Target Model:</span>
                          <span className="col-span-2 text-white font-mono">{inspectLog.target_model || "—"}</span>
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          <span className="text-gray-500">Risk Level:</span>
                          <span className="col-span-2 text-white capitalize">{inspectLog.risk_level}</span>
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          <span className="text-gray-500">Actor ID:</span>
                          <span className="col-span-2 text-white font-mono text-[10px]">{inspectLog.actor_user_id || "System"}</span>
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          <span className="text-gray-500">Identity Mode:</span>
                          <span className="col-span-2 text-white capitalize">{inspectLog.identity_mode}</span>
                        </div>
                        
                        <div className="pt-2 border-t border-[#1e293b]/50">
                          <span className="text-gray-500 block mb-1">Raw Payload Details:</span>
                          <pre className="p-3 bg-[#070b15] border border-gray-800 rounded-lg overflow-x-auto text-[10px] font-mono text-gray-300 max-h-48 overflow-y-auto">
                            {JSON.stringify(inspectLog, null, 2)}
                          </pre>
                        </div>
                      </div>
                    ) : (
                      <div className="text-center py-12 text-gray-500 select-none">
                        <Eye className="w-8 h-8 text-gray-700 mx-auto mb-2" />
                        Select an event from the list to inspect payload details.
                      </div>
                    )}
                  </div>

                </div>
              )}
            </div>
          )}

          {/* SETTINGS / SYSTEM CONFIG VIEW */}
          {activeTab === "settings" && hasRole(["AIPlatform.Admin", "AIPlatform.Developer"]) && (
            <div className="max-w-4xl mx-auto space-y-8 select-text">
              <div className="p-6 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] space-y-4">
                <h3 className="font-bold text-lg text-white select-none">Active Profile</h3>
                
                <div className="grid grid-cols-4 gap-4 items-center p-4 border border-[#1e293b]/50 rounded-xl bg-[#070b15] text-sm">
                  <div className="w-12 h-12 rounded-lg bg-indigo-600/10 border border-indigo-500/25 flex items-center justify-center">
                    <User className="w-6 h-6 text-indigo-400" />
                  </div>
                  <div className="col-span-3">
                    <p className="font-semibold text-white">{activeUser.displayName}</p>
                    <p className="text-xs text-gray-500 font-mono mt-0.5">Email: {activeUser.email}</p>
                    <p className="text-xs text-gray-500 font-mono">Assigned Roles: {activeUser.roles.join(", ")}</p>
                  </div>
                </div>
              </div>

              <div className="p-6 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] space-y-4">
                <h3 className="font-bold text-lg text-white select-none">Platform Configurations</h3>
                
                <div className="space-y-4 text-sm font-medium">
                  <div className="flex justify-between p-3 border-b border-[#1e293b]/50">
                    <span className="text-gray-400">Database Engine</span>
                    <span className="text-white font-mono">PostgreSQL 16 (Azure Flexible Server)</span>
                  </div>
                  <div className="flex justify-between p-3 border-b border-[#1e293b]/50">
                    <span className="text-gray-400">Secrets Vault</span>
                    <span className="text-white font-mono">Azure Key Vault (RBAC-Gated)</span>
                  </div>
                  <div className="flex justify-between p-3 border-b border-[#1e293b]/50">
                    <span className="text-gray-400">Odoo Core Engine</span>
                    <span className="text-white font-mono">v1.0.0 (FastAPI Core Proxy)</span>
                  </div>
                </div>
              </div>
            </div>
          )}

        </section>
      </main>
    </div>
  );
}
