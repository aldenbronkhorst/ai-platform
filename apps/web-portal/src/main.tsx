import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { PublicClientApplication } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { msalConfig } from "./authConfig";
import "./index.css";
import App from "./App.tsx";

// Initialize MSAL PublicClientApplication
const msalInstance = new PublicClientApplication(msalConfig);

// MSAL v3 requires asynchronous initialization and handling the redirect promise before rendering
msalInstance.initialize().then(async () => {
  try {
    const response = await msalInstance.handleRedirectPromise();

    if (response?.account) {
      msalInstance.setActiveAccount(response.account);
    } else {
      const accounts = msalInstance.getAllAccounts();
      if (accounts.length > 0) {
        msalInstance.setActiveAccount(accounts[0]);
      }
    }
  } catch (err) {
    console.error("MSAL redirect handling failed:", err);
  }

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
