import type { Configuration, PopupRequest } from "@azure/msal-browser";

const portalClientId = "ff6a9526-c27a-42a6-b317-56060d11b14e";
const apiClientId = "fcefb508-bb9d-4d5d-b1c5-6d2ef04c0208";

// Config defaults
export const msalConfig: Configuration = {
  auth: {
    clientId: import.meta.env.VITE_ENTRA_CLIENT_ID || portalClientId,
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_ENTRA_TENANT_ID || "03af606c-d85a-48ff-ad4b-a5a8895a6d98"}`,
    redirectUri: typeof window !== "undefined" ? window.location.origin : "https://ai.lotslotsmore.com",
    postLogoutRedirectUri: typeof window !== "undefined" ? window.location.origin : "https://ai.lotslotsmore.com",
  },
  cache: {
    cacheLocation: "sessionStorage",
  }
};

// Add scopes here for active directory token acquisition (Access Token for AI Core API)
export const loginRequest: PopupRequest = {
  scopes: [`api://${apiClientId}/access_as_user`]
};

// Graph API endpoint config
export const graphConfig = {
  graphMeEndpoint: "https://graph.microsoft.com/v1.0/me"
};
