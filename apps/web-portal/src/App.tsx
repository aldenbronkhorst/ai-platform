import React, { useState, useEffect } from "react";
import { useMsal, useIsAuthenticated } from "@azure/msal-react";
import { loginRequest } from "./authConfig";
import { 
  MessageSquare, 
  Database, 
  Briefcase, 
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
  Cpu,
  Eye,
  Key,
  ExternalLink,
  BookOpen
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
  role: string;
}

export default function App() {
  const { instance, accounts } = useMsal();
  const msalAuthenticated = useIsAuthenticated();

  // Tab State
  const [activeTab, setActiveTab] = useState<string>("chat");
  const [activeEnvironment, setActiveEnvironment] = useState<string>("production");

  // Local Mock Auth States (local-only)
  const [localMockAuthenticated, setLocalMockAuthenticated] = useState<boolean>(false);
  const [localMockUser, setLocalMockUser] = useState<UserProfile | null>(null);

  // Unified active user derived from active auth method
  const [activeUser, setActiveUser] = useState<UserProfile | null>(null);
  const [accessToken, setAccessToken] = useState<string>("");

  // Chat States
  const [chatMessages, setChatMessages] = useState<Array<{ sender: "user" | "bot"; text: string; timestamp: Date; systemInfo?: any }>>([
    { sender: "bot", text: "Hello! I am your AI Core assistant. I am connected securely to the AI Platform and have full Odoo Connector capability. How can I help you manage your business systems today?", timestamp: new Date() }
  ]);
  const [chatInput, setChatInput] = useState<string>("");
  const [isChatSending, setIsChatSending] = useState<boolean>(false);
  const [useOdooJob, setUseOdooJob] = useState<boolean>(false);

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

  // Artifacts States
  const [artifacts, setArtifacts] = useState<any[]>([]);
  const [isArtifactsLoading, setIsArtifactsLoading] = useState<boolean>(false);

  // Synchronize active authentication session
  useEffect(() => {
    if (msalAuthenticated && accounts.length > 0) {
      const activeAccount = accounts[0];
      setActiveUser({
        email: activeAccount.username,
        displayName: activeAccount.name || activeAccount.username,
        role: "user" // App roles will be read and validated securely by backend JWT validation
      });
      
      // Acquire JWT access token silently
      instance.acquireTokenSilent({
        ...loginRequest,
        account: activeAccount
      }).then(response => {
        setAccessToken(response.accessToken);
      }).catch(err => {
        console.warn("Silent token acquisition failed, prompting interactive login:", err);
      });
    } else if (ENABLE_LOCAL_MOCK && localMockAuthenticated && localMockUser) {
      setActiveUser(localMockUser);
      setAccessToken("mock-local-token");
    } else {
      setActiveUser(null);
      setAccessToken("");
    }
  }, [msalAuthenticated, accounts, localMockAuthenticated, localMockUser]);

  // Fetch Odoo Status, Audit Logs, Jobs on tab switches
  useEffect(() => {
    if (!accessToken) return;
    if (activeTab === "connected-accounts") fetchOdooStatus();
    if (activeTab === "audit") fetchAuditLogs();
    if (activeTab === "jobs") fetchJobs();
    if (activeTab === "artifacts") fetchArtifacts();
  }, [activeTab, accessToken]);

  const getRequestHeaders = () => {
    return {
      "Authorization": `Bearer ${accessToken}`,
      "Content-Type": "application/json"
    };
  };

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

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim() || !accessToken) return;

    const userMsg = chatInput;
    setChatInput("");
    setChatMessages(prev => [...prev, { sender: "user", text: userMsg, timestamp: new Date() }]);
    setIsChatSending(true);

    try {
      const isOdooQuery = userMsg.toLowerCase().includes("odoo") || userMsg.toLowerCase().includes("partner") || userMsg.toLowerCase().includes("customer") || userMsg.toLowerCase().includes("read");
      let botResponse = "";
      let systemInfo = null;

      if (isOdooQuery) {
        const response = await fetch(`${APIM_BASE_URL}/tools/odoo/search-read`, {
          method: "POST",
          headers: getRequestHeaders(),
          body: JSON.stringify({
            model: "res.partner",
            limit: 3,
            create_job: useOdooJob,
            job_title: `Chat query: ${userMsg.slice(0, 30)}`
          })
        });
        const data = await response.json();
        if (response.ok) {
          botResponse = `I called the Odoo Connector API securely. Found ${data.records?.length || 0} partner records:\n\n` + 
            data.records.map((r: any) => `• **${r.name}** (ID: ${r.id}, Email: ${r.email || "No email"})`).join("\n") +
            (data._job ? `\n\n💼 **Platform Job Linked:** Created Job ID \`${data._job.job_id}\` and uploaded JSON result as Blob Artifact ID \`${data._job.artifact_id}\`` : "");
          systemInfo = { apiCalled: "POST /tools/odoo/search-read", status: "200 OK", recordsCount: data.records?.length };
        } else {
          botResponse = `Odoo tool call failed: ${data.detail || "Credentials missing or connection issue."}`;
          systemInfo = { apiCalled: "POST /tools/odoo/search-read", status: `${response.status}`, error: data.detail };
        }
      } else {
        botResponse = `I received your message: "${userMsg}". How can I assist you with your business workflows, or should I retrieve some info from Odoo? Let me know!`;
      }

      setChatMessages(prev => [...prev, { sender: "bot", text: botResponse, timestamp: new Date(), systemInfo }]);
    } catch (err: any) {
      setChatMessages(prev => [...prev, { sender: "bot", text: `Error calling API Core: ${err.message}`, timestamp: new Date() }]);
    } finally {
      setIsChatSending(false);
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

  const cycleEnvironment = () => {
    if (activeEnvironment === "production") {
      setActiveEnvironment("staging");
    } else if (activeEnvironment === "staging") {
      setActiveEnvironment("development");
    } else {
      setActiveEnvironment("production");
    }
  };

  // ENTRA LOGIN SCREEN FOR PRODUCTION
  if (!activeUser) {
    return (
      <div className="flex h-screen bg-[#070b15] text-[#f3f4f6] font-sans antialiased overflow-hidden items-center justify-center relative">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(99,102,241,0.08),transparent_50%)]" />
        
        <div className="relative z-10 max-w-md w-full bg-[#0a0f1d] border border-[#1e293b] rounded-2xl p-8 shadow-2xl text-center space-y-6">
          <div className="mx-auto w-16 h-16 rounded-2xl bg-indigo-600/10 border border-indigo-500/25 flex items-center justify-center mb-4">
            <Bot className="w-8 h-8 text-indigo-400" />
          </div>
          
          <div>
            <h2 className="text-2xl font-bold text-white tracking-tight">AI Platform Portal</h2>
            <p className="text-sm text-gray-400 mt-2">Sign in using your corporate Microsoft identity to securely access tools.</p>
          </div>

          <div className="space-y-3 pt-4">
            {/* Real Microsoft Entra ID Login Button */}
            <button 
              onClick={() => instance.loginRedirect(loginRequest)}
              className="w-full py-3 bg-white hover:bg-gray-100 text-gray-900 font-bold rounded-xl text-sm transition-all flex items-center justify-center gap-3 shadow-lg cursor-pointer"
            >
              {/* Microsoft colored logo */}
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
                    role: "admin"
                  });
                  setLocalMockAuthenticated(true);
                }}
                className="w-full py-3 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-white font-bold rounded-xl text-sm transition-all flex items-center justify-center gap-2 cursor-pointer"
              >
                <User className="w-4 h-4 text-indigo-400" />
                Local Mock Sign In (Developer)
              </button>
            )}
          </div>

          <div className="border-t border-[#1e293b]/50 pt-4 flex items-center justify-between text-xs text-gray-500">
            <span>Microsoft Security Active</span>
            <span>v1.0.0</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-[#070b15] text-[#f3f4f6] font-sans antialiased overflow-hidden">
      
      {/* SIDEBAR */}
      <aside className="w-64 bg-[#0a0f1d] border-r border-[#1e293b] flex flex-col justify-between select-none">
        <div>
          {/* Logo */}
          <div className="p-6 border-b border-[#1e293b] flex items-center gap-3">
            <div className="p-2 bg-indigo-600/25 border border-indigo-500/50 rounded-xl">
              <Bot className="w-6 h-6 text-indigo-400" />
            </div>
            <div>
              <h1 className="font-bold text-lg leading-tight tracking-wide text-white">AI Platform</h1>
              <span className="text-xs text-indigo-400 font-medium tracking-widest uppercase">Portal</span>
            </div>
          </div>

          {/* Nav Items */}
          <nav className="p-4 space-y-1">
            <button 
              onClick={() => setActiveTab("chat")}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all ${activeTab === "chat" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/20 hover:text-gray-200 border border-transparent"}`}
            >
              <MessageSquare className="w-4 h-4" />
              Chat Assistant
            </button>

            <button 
              onClick={() => setActiveTab("connected-accounts")}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all ${activeTab === "connected-accounts" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/20 hover:text-gray-200 border border-transparent"}`}
            >
              <Database className="w-4 h-4" />
              Connected Accounts
            </button>

            <button 
              onClick={() => setActiveTab("jobs")}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all ${activeTab === "jobs" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/20 hover:text-gray-200 border border-transparent"}`}
            >
              <Briefcase className="w-4 h-4" />
              Jobs Dashboard
            </button>

            <button 
              onClick={() => setActiveTab("artifacts")}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all ${activeTab === "artifacts" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/20 hover:text-gray-200 border border-transparent"}`}
            >
              <FileText className="w-4 h-4" />
              Artifacts List
            </button>

            <button 
              onClick={() => setActiveTab("audit")}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all ${activeTab === "audit" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/20 hover:text-gray-200 border border-transparent"}`}
            >
              <ShieldAlert className="w-4 h-4" />
              Audit Logs
            </button>

            <button 
              onClick={() => setActiveTab("settings")}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all ${activeTab === "settings" ? "bg-indigo-600/15 border border-indigo-500/50 text-white" : "text-gray-400 hover:bg-gray-800/20 hover:text-gray-200 border border-transparent"}`}
            >
              <Settings className="w-4 h-4" />
              Admin & Settings
            </button>
          </nav>
        </div>

        {/* User profile section */}
        <div className="p-4 border-t border-[#1e293b]">
          <div className="flex items-center gap-3 p-2 rounded-xl bg-gray-800/10 border border-gray-800/50">
            <div className="w-10 h-10 rounded-lg bg-indigo-600/20 border border-indigo-500/35 flex items-center justify-center">
              <User className="w-5 h-5 text-indigo-400" />
            </div>
            <div className="overflow-hidden">
              <p className="text-sm font-semibold text-white truncate">{activeUser.displayName}</p>
              <span className="text-xs text-gray-500 truncate block">Microsoft ID Active</span>
            </div>
          </div>
        </div>
      </aside>

      {/* MAIN CONTAINER */}
      <main className="flex-1 flex flex-col overflow-hidden">
        
        {/* HEADER */}
        <header className="h-16 bg-[#0a0f1d] border-b border-[#1e293b] px-8 flex justify-between items-center select-none">
          <div className="flex items-center gap-4">
            <span className="text-xs uppercase tracking-widest text-indigo-400 font-bold">{activeTab}</span>
            <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse" />
          </div>

          <div className="flex items-center gap-4">
            {/* Env picker */}
            <button 
              onClick={cycleEnvironment}
              className="flex items-center gap-2 px-3 py-1.5 bg-gray-800/20 hover:bg-gray-800/40 border border-gray-800 rounded-lg cursor-pointer transition-all"
              title="Click to cycle environments"
            >
              <span className={`w-2 h-2 rounded-full ${
                activeEnvironment === "production" ? "bg-emerald-500" : activeEnvironment === "staging" ? "bg-amber-500" : "bg-indigo-500"
              }`} />
              <span className="text-xs font-semibold text-white uppercase">{activeEnvironment}</span>
            </button>

            {/* Profile logout */}
            <button 
              onClick={handleSignOut}
              className="p-2 text-gray-400 hover:text-white rounded-lg hover:bg-gray-800/50 transition-all cursor-pointer"
              title="Logout"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </header>

        {/* ACTIVE ROUTE CONTAINER */}
        <section className="flex-1 overflow-y-auto p-8 bg-[#070b15] relative">
          
          {/* CHAT TAB */}
          {activeTab === "chat" && (
            <div className="h-full flex flex-col justify-between max-w-4xl mx-auto border border-[#1e293b] rounded-2xl bg-[#0a0f1d]/50 overflow-hidden">
              {/* Message flow */}
              <div className="flex-1 overflow-y-auto p-6 space-y-6">
                {chatMessages.map((msg, idx) => (
                  <div key={idx} className={`flex gap-4 ${msg.sender === "user" ? "justify-end" : "justify-start"}`}>
                    
                    {msg.sender === "bot" && (
                      <div className="w-8 h-8 rounded-lg bg-indigo-600/20 border border-indigo-500/50 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-indigo-400" />
                      </div>
                    )}

                    <div className={`max-w-[75%] p-4 rounded-2xl border text-sm leading-relaxed whitespace-pre-wrap ${
                      msg.sender === "user" 
                        ? "bg-indigo-600/10 border-indigo-500/40 text-indigo-50 rounded-tr-none" 
                        : "bg-gray-800/35 border-gray-800 text-gray-200 rounded-tl-none"
                    }`}>
                      {msg.text}

                      {/* Display system diagnostics below bot messages */}
                      {msg.systemInfo && (
                        <div className="mt-4 pt-3 border-t border-gray-800 flex items-center justify-between text-xs text-indigo-400 font-mono">
                          <span className="flex items-center gap-1.5"><Cpu className="w-3.5 h-3.5" /> {msg.systemInfo.apiCalled}</span>
                          <span className="bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded border border-emerald-500/25 font-bold">{msg.systemInfo.status}</span>
                        </div>
                      )}
                    </div>

                    {msg.sender === "user" && (
                      <div className="w-8 h-8 rounded-lg bg-gray-800/50 border border-gray-700 flex items-center justify-center shrink-0">
                        <User className="w-4 h-4 text-gray-300" />
                      </div>
                    )}

                  </div>
                ))}
              </div>

              {/* Input section */}
              <div className="p-4 border-t border-[#1e293b] bg-[#0a0f1d] flex flex-col gap-3">
                <form onSubmit={handleSendMessage} className="flex gap-3">
                  <input 
                    type="text"
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    placeholder="Ask AI Core anything, try 'Read res.partner from Odoo'..."
                    disabled={isChatSending}
                    className="flex-1 px-4 py-3 bg-[#070b15] border border-[#1e293b] rounded-xl focus:outline-none focus:border-indigo-500 text-sm placeholder-gray-500"
                  />
                  <button 
                    type="submit"
                    disabled={isChatSending || !chatInput.trim()}
                    className="px-6 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white font-medium rounded-xl text-sm transition-all flex items-center gap-2 cursor-pointer"
                  >
                    {isChatSending ? "Sending..." : "Submit"}
                    <ArrowRight className="w-4 h-4" />
                  </button>
                </form>

                {/* Additional controls */}
                <div className="flex items-center justify-between px-2 text-xs text-gray-500 select-none">
                  <span className="flex items-center gap-2">
                    <input 
                      type="checkbox" 
                      id="useOdooJob"
                      checked={useOdooJob}
                      onChange={(e) => setUseOdooJob(e.target.checked)}
                      className="rounded border-[#1e293b] text-indigo-600 focus:ring-0 bg-[#070b15] cursor-pointer"
                    />
                    <label htmlFor="useOdooJob" className="cursor-pointer">Create Platform Job & upload JSON results to Blob Storage</label>
                  </span>
                  <span>Press Submit to secure Odoo proxy query</span>
                </div>
              </div>
            </div>
          )}

          {/* CONNECTED ACCOUNTS TAB */}
          {activeTab === "connected-accounts" && (
            <div className="max-w-5xl mx-auto space-y-8">
              {/* Intro card */}
              <div className="p-8 border border-[#1e293b] rounded-2xl bg-gradient-to-r from-indigo-900/10 to-transparent flex items-center justify-between">
                <div>
                  <h2 className="text-xl font-bold text-white mb-2">Connected Accounts</h2>
                  <p className="text-sm text-gray-400 max-w-2xl">Connect and manage third-party service connections securely on behalf of your profile. Keys and tokens are stored safely in Microsoft Azure Key Vault.</p>
                </div>
                <BookOpen className="w-12 h-12 text-indigo-500/25 shrink-0" />
              </div>

              {/* Accounts list grid */}
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

                      {/* Status pill */}
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

                  {/* Actions */}
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

                {/* PLACEHOLDER CARD */}
                <div className="p-6 border border-[#1e293b]/50 border-dashed rounded-2xl bg-transparent flex flex-col justify-center items-center text-center p-8 select-none">
                  <Database className="w-8 h-8 text-gray-600 mb-3" />
                  <h4 className="font-bold text-gray-400 mb-1">Microsoft / Microsoft 365</h4>
                  <p className="text-xs text-gray-500 max-w-xs">Connecting SharePoint, Outlook and Microsoft Graph is deferred to next platform iteration.</p>
                </div>

              </div>

              {/* Form Validation Feedback */}
              {testResult && (
                <div className={`p-4 border rounded-xl flex items-start gap-3 text-sm ${testResult.success ? "bg-emerald-500/10 border-emerald-500/25 text-emerald-400" : "bg-rose-500/10 border-rose-500/25 text-rose-400"}`}>
                  {testResult.success ? <CheckCircle2 className="w-5 h-5 shrink-0" /> : <XCircle className="w-5 h-5 shrink-0" />}
                  <div>
                    <p className="font-semibold">{testResult.success ? "Verification Success" : "Verification Failed"}</p>
                    <p className="mt-0.5">{testResult.message}</p>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* CONNECT ODOO MODAL */}
          {isConnectOpen && (
            <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
              <div className="bg-[#0a0f1d] border border-[#1e293b] rounded-2xl max-w-lg w-full overflow-hidden shadow-2xl">
                <div className="p-6 border-b border-[#1e293b] flex justify-between items-center select-none">
                  <h3 className="font-bold text-lg text-white">Connect Odoo Enterprise</h3>
                  <button onClick={() => setIsConnectOpen(false)} className="text-gray-400 hover:text-white">✕</button>
                </div>
                <form onSubmit={handleConnectOdoo} className="p-6 space-y-4">
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

          {/* ROTATE KEY MODAL */}
          {isRotateOpen && (
            <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
              <div className="bg-[#0a0f1d] border border-[#1e293b] rounded-2xl max-w-md w-full overflow-hidden shadow-2xl">
                <div className="p-6 border-b border-[#1e293b] flex justify-between items-center select-none">
                  <h3 className="font-bold text-lg text-white">Rotate API Key</h3>
                  <button onClick={() => setIsRotateOpen(false)} className="text-gray-400 hover:text-white">✕</button>
                </div>
                <form onSubmit={handleRotateOdoo} className="p-6 space-y-4">
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

          {/* JOBS TAB */}
          {activeTab === "jobs" && (
            <div className="max-w-6xl mx-auto space-y-6">
              <div className="flex justify-between items-center">
                <div>
                  <h2 className="text-xl font-bold text-white">Jobs Dashboard</h2>
                  <p className="text-sm text-gray-400 mt-1">Monitor background workflow executions and automated agent processes.</p>
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
                <div className="text-center py-20 text-gray-400">Loading jobs...</div>
              ) : jobs.length === 0 ? (
                <div className="p-8 border border-[#1e293b]/50 border-dashed rounded-2xl bg-transparent text-center py-16 text-gray-400 select-none">
                  <Briefcase className="w-10 h-10 text-gray-600 mb-3 mx-auto" />
                  <p className="font-semibold text-gray-300">No active jobs found</p>
                  <p className="text-xs text-gray-500 max-w-sm mx-auto mt-1">To trigger a platform job, enable "Create Platform Job" in the Chat Assistant view before calling Odoo tools.</p>
                </div>
              ) : (
                <div className="border border-[#1e293b] rounded-2xl bg-[#0a0f1d] overflow-hidden select-text">
                  <table className="w-full text-left border-collapse text-sm">
                    <thead>
                      <tr className="bg-gray-800/30 border-b border-[#1e293b] text-gray-400 text-xs font-bold uppercase tracking-wider select-none">
                        <th className="p-4">Title</th>
                        <th className="p-4">Workflow</th>
                        <th className="p-4">Status</th>
                        <th className="p-4">System Linked</th>
                        <th className="p-4">Created At</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#1e293b]/50">
                      {jobs.map((job) => (
                        <tr key={job.id} className="hover:bg-gray-800/10">
                          <td className="p-4 font-semibold text-white">{job.title}</td>
                          <td className="p-4 font-mono text-xs">{job.workflow_type}</td>
                          <td className="p-4">
                            <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium ${
                              job.status === "completed" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                            }`}>
                              <span className={`w-1.5 h-1.5 rounded-full ${job.status === "completed" ? "bg-emerald-400" : "bg-amber-400 animate-pulse"}`} />
                              {job.status}
                            </span>
                          </td>
                          <td className="p-4">
                            <span className="bg-gray-800/50 text-gray-300 border border-gray-800 px-2 py-0.5 rounded font-mono text-xs capitalize">{job.linked_system}</span>
                          </td>
                          <td className="p-4 text-xs font-mono text-gray-400">{new Date(job.created_at).toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* ARTIFACTS TAB */}
          {activeTab === "artifacts" && (
            <div className="max-w-6xl mx-auto space-y-6">
              <div className="flex justify-between items-center">
                <div>
                  <h2 className="text-xl font-bold text-white">Artifacts Dashboard</h2>
                  <p className="text-sm text-gray-400 mt-1">View extracted, parsed, and intermediate deliverable files securely mapped to jobs.</p>
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
                <div className="text-center py-20 text-gray-400">Loading artifacts...</div>
              ) : artifacts.length === 0 ? (
                <div className="p-8 border border-[#1e293b]/50 border-dashed rounded-2xl bg-transparent text-center py-16 text-gray-400 select-none">
                  <FileText className="w-10 h-10 text-gray-600 mb-3 mx-auto" />
                  <p className="font-semibold text-gray-300">No storage artifacts found</p>
                  <p className="text-xs text-gray-500 max-w-sm mx-auto mt-1">Triggered jobs automatically upload formatted JSON deliverables to private Azure Blob containers.</p>
                </div>
              ) : (
                <div className="grid md:grid-cols-3 gap-6 select-text">
                  {artifacts.map((art) => (
                    <div key={art.id} className="p-5 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] flex flex-col justify-between">
                      <div>
                        <div className="flex justify-between items-start mb-3">
                          <span className="bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 px-2 py-0.5 rounded text-[10px] font-mono uppercase">{art.artifact_type}</span>
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
                          <ExternalLink className="w-3 h-3" />
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* AUDIT LOG TAB */}
          {activeTab === "audit" && (
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

          {/* SETTINGS TAB */}
          {activeTab === "settings" && (
            <div className="max-w-4xl mx-auto space-y-8">
              <div className="p-6 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] space-y-4">
                <h3 className="font-bold text-lg text-white">Active Profile</h3>
                
                <div className="grid grid-cols-4 gap-4 items-center p-4 border border-[#1e293b]/50 rounded-xl bg-[#070b15] text-sm">
                  <div className="w-12 h-12 rounded-lg bg-indigo-600/10 border border-indigo-500/25 flex items-center justify-center">
                    <User className="w-6 h-6 text-indigo-400" />
                  </div>
                  <div className="col-span-3">
                    <p className="font-semibold text-white">{activeUser.displayName}</p>
                    <p className="text-xs text-gray-500 font-mono mt-0.5">Email: {activeUser.email}</p>
                    <p className="text-xs text-gray-500 font-mono">ID: {activeUser.id || "Entra Federated ID"}</p>
                  </div>
                </div>
              </div>

              <div className="p-6 border border-[#1e293b] rounded-2xl bg-[#0a0f1d] space-y-4">
                <h3 className="font-bold text-lg text-white">Platform Configurations</h3>
                
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
