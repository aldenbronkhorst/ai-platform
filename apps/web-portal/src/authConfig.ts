import type { Configuration, PopupRequest } from "@azure/msal-browser";

// Config defaults
export const msalConfig: Configuration = {
  auth: {
    clientId: import.meta.env.VITE_ENTRA_CLIENT_ID || "9067d9d9-b0bf-4d56-be8f-8d5bc3bc06b5",
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_ENTRA_TENANT_ID || "03af606c-d85a-48ff-ad4b-a5a8895a6d98"}`,
    redirectUri: typeof window !== "undefined" ? window.location.origin : "/",
  },
  cache: {
    cacheLocation: "sessionStorage",
  }
};

// Add scopes here for active directory token acquisition
export const loginRequest: PopupRequest = {
  scopes: ["User.Read"]
};

// Graph API endpoint config
export const graphConfig = {
  graphMeEndpoint: "https://graph.microsoft.com/v1.0/me"
};
