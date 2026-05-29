import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { PublicClientApplication, EventType } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { msalConfig } from "./authConfig";
import "./index.css";
import App from "./App.tsx";

// Initialize MSAL PublicClientApplication
const msalInstance = new PublicClientApplication(msalConfig);

// MSAL v3 requires asynchronous initialization before rendering
msalInstance.initialize().then(() => {
  // Set active account if accounts are already present (e.g. on page refresh)
  const accounts = msalInstance.getAllAccounts();
  if (accounts.length > 0) {
    msalInstance.setActiveAccount(accounts[0]);
  }

  // Listen for successful login events and set active account automatically
  msalInstance.addEventCallback((event) => {
    if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
      const payload = event.payload as any;
      const account = payload.account;
      msalInstance.setActiveAccount(account);
    }
  });

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <MsalProvider instance={msalInstance}>
        <App />
      </MsalProvider>
    </StrictMode>,
  );
}).catch(err => {
  console.error("MSAL initialization failed:", err);
});
