import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { PublicClientApplication, EventType } from "@azure/msal-browser";
import type { AuthenticationResult } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { msalConfig } from "./authConfig";
import "./index.css";
import App from "./App.tsx";

const msalInstance = new PublicClientApplication(msalConfig);

msalInstance.initialize().then(async () => {
  let startupAuthError: string | null = null;

  try {
    const redirectResponse = await msalInstance.handleRedirectPromise();

    if (redirectResponse?.account) {
      msalInstance.setActiveAccount(redirectResponse.account);
    } else {
      const accounts = msalInstance.getAllAccounts();
      if (accounts.length > 0) {
        msalInstance.setActiveAccount(accounts[0]);
      }
    }
  } catch (err: any) {
    startupAuthError = err.message || String(err);
    console.error("MSAL redirect processing failed:", err);
  }

  msalInstance.addEventCallback((event) => {
    if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
      const payload = event.payload as AuthenticationResult;
      if (payload.account) {
        msalInstance.setActiveAccount(payload.account);
      }
    }
  });

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <MsalProvider instance={msalInstance}>
        <App startupAuthError={startupAuthError} />
      </MsalProvider>
    </StrictMode>
  );
}).catch(err => {
  console.error("MSAL initialization failed:", err);
});
