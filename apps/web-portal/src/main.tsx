import { StrictMode, Component } from "react";
import type { ReactNode, ErrorInfo } from "react";
import { createRoot } from "react-dom/client";
import { PublicClientApplication, EventType } from "@azure/msal-browser";
import type { AuthenticationResult } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { msalConfig } from "./authConfig";
import { rememberAuthAccount } from "./authSession";
import { ThemeProvider } from "./theme";
import "./index.css";
import App from "./App.tsx";

class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean; error: string }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, error: "" };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error: error.message };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("App crashed:", error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 40, textAlign: "center", fontFamily: "sans-serif" }}>
          <h2>Something went wrong</h2>
          <p style={{ color: "#666" }}>{this.state.error}</p>
          <button onClick={() => window.location.reload()} style={{ padding: "8px 16px", cursor: "pointer" }}>
            Reload page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

if (import.meta.env.DEV && window.location.pathname === "/provider-preview") {
  import("./pages/AIProvidersPage").then(({ AIProvidersPage }) => {
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <ErrorBoundary>
          <ThemeProvider>
            <div className="min-h-screen bg-canvas p-4 text-default sm:p-6">
              <AIProvidersPage accessToken="" />
            </div>
          </ThemeProvider>
        </ErrorBoundary>
      </StrictMode>
    );
  }).catch(err => {
    console.error("Provider preview failed:", err);
  });
} else {
  const msalInstance = new PublicClientApplication(msalConfig);

  msalInstance.initialize().then(async () => {
    try {
      const redirectResponse = await msalInstance.handleRedirectPromise();

      if (redirectResponse?.account) {
        msalInstance.setActiveAccount(redirectResponse.account);
        rememberAuthAccount(redirectResponse.account);
      } else {
        const accounts = msalInstance.getAllAccounts();
        if (accounts.length > 0) {
          msalInstance.setActiveAccount(accounts[0]);
          rememberAuthAccount(accounts[0]);
        }
      }
    } catch (err: unknown) {
      console.error("MSAL redirect processing failed:", err);
    }

    msalInstance.addEventCallback((event) => {
      if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
        const payload = event.payload as AuthenticationResult;
        if (payload.account) {
          msalInstance.setActiveAccount(payload.account);
          rememberAuthAccount(payload.account);
        }
      }
    });

    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <ErrorBoundary>
          <ThemeProvider>
            <MsalProvider instance={msalInstance}>
              <App />
            </MsalProvider>
          </ThemeProvider>
        </ErrorBoundary>
      </StrictMode>
    );
  }).catch(err => {
    console.error("MSAL initialization failed:", err);
  });
}
