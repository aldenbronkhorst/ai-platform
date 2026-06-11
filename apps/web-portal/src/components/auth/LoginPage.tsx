import { AlertTriangle, Shield, RefreshCw } from "lucide-react";
import type { InteractionStatus } from "@azure/msal-browser";
import type { AccountInfo, IPublicClientApplication, RedirectRequest } from "@azure/msal-browser";
import { GlassButton } from "../ui/GlassButton";


interface LoginPageProps {
  inProgress: InteractionStatus;
  authError: string | null;
  startupAuthError: string | null;
  showDiagnostics: boolean;
  onSignIn: () => void;
  onToggleDiagnostics: () => void;
  instance: Pick<IPublicClientApplication, "acquireTokenRedirect">;
  loginRequest: RedirectRequest;
  accounts: AccountInfo[];
}

export function LoginPage({
  inProgress,
  authError,
  startupAuthError,
  showDiagnostics,
  onSignIn,
  onToggleDiagnostics,
  instance,
  loginRequest,
  accounts,
}: LoginPageProps) {
  if (inProgress !== "none") {
    return (
      <div className="flex h-[100dvh] bg-canvas text-default items-center justify-center relative overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,var(--color-accent-soft),transparent_50%)]" />
        <div className="relative z-10 text-center space-y-4">
          <RefreshCw className="w-10 h-10 text-muted animate-spin mx-auto" />
          <p className="text-sm font-semibold tracking-wide text-muted">
            Completing Microsoft sign-in...
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-[100dvh] bg-canvas text-default antialiased overflow-hidden items-center justify-center relative px-4">
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-[var(--color-accent-soft)] rounded-full blur-[120px] pointer-events-none" />
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-[var(--color-accent-soft)] rounded-full blur-[120px] pointer-events-none" />

      <div className="relative z-10 max-w-md w-full glass-panel rounded-3xl p-8 text-center space-y-6">
        <div>
          <h2 className="text-2xl font-extrabold text-default tracking-tight">
            AI Platform
          </h2>
          <p className="text-xs text-muted mt-2 leading-relaxed">
            Secure operational portal for business operations and automation.
          </p>
        </div>

        {(authError || startupAuthError) && (
          <div className="p-4 border border-[var(--color-danger)]/25 bg-[var(--color-danger)]/10 text-[var(--color-danger)] text-xs rounded-2xl space-y-3 text-left animate-fade-in">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <div>
                <p className="font-bold">Authentication Failed</p>
                <p className="mt-0.5 opacity-90">{authError || startupAuthError}</p>
              </div>
            </div>
            <button
              onClick={() => instance.acquireTokenRedirect(loginRequest)}
              className="w-full py-2 bg-[var(--color-danger)]/15 hover:bg-[var(--color-danger)]/25 border border-[var(--color-danger)]/40 text-[var(--color-danger)] font-bold rounded-xl text-[11px] transition-all"
            >
              Continue Microsoft Permission
            </button>
          </div>
        )}

        <div className="space-y-3 pt-4">
          <GlassButton onClick={onSignIn} className="w-full py-3.5 text-sm font-extrabold">
            <div className="grid grid-cols-2 gap-0.5 shrink-0 w-4 h-4">
              <div className="bg-[#f25f22] w-1.5 h-1.5" />
              <div className="bg-[#7fba00] w-1.5 h-1.5" />
              <div className="bg-[#00a4ef] w-1.5 h-1.5" />
              <div className="bg-[#ffb900] w-1.5 h-1.5" />
            </div>
            Sign in with Microsoft ID
          </GlassButton>

        </div>

        <button
          onClick={onToggleDiagnostics}
          className="text-[11px] text-soft hover-text-default underline select-none block mx-auto"
        >
          {showDiagnostics ? "Hide Security Diagnostics" : "Show Security Diagnostics"}
        </button>

        {showDiagnostics && (
          <div className="border border-default p-4 bg-subtle rounded-2xl text-left font-mono text-[10px] text-muted space-y-1 select-text">
            <p className="text-muted font-bold border-b border-default pb-1 mb-1.5 flex items-center gap-1.5">
              <Shield className="w-3.5 h-3.5" /> Security Diagnostics
            </p>
            <p>
              <span className="text-soft">inProgress:</span> {inProgress}
            </p>
            <p>
              <span className="text-soft">accounts.length:</span> {accounts.length}
            </p>
            <p>
              <span className="text-soft">startupAuthError:</span>{" "}
              {startupAuthError || "None"}
            </p>
            <p>
              <span className="text-soft">lastError:</span> {authError || "None"}
            </p>
          </div>
        )}

        <div className="border-t border-default pt-4 flex items-center justify-between text-xs text-muted select-none">
          <span>Microsoft Security Active</span>
          <span>v1.0.0</span>
        </div>
      </div>
    </div>
  );
}
