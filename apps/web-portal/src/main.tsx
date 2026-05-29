import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { PublicClientApplication } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { msalConfig } from "./authConfig";
import "./index.css";
import App from "./App.tsx";

// Initialize MSAL PublicClientApplication
const msalInstance = new PublicClientApplication(msalConfig);

// MSAL v3 requires asynchronous initialization before any rendering or authentication attempts
msalInstance.initialize().then(() => {
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
