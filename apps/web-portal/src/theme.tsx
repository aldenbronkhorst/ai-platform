import { useState, useEffect, createContext, useContext, useCallback, type ReactNode } from "react";

type Theme = "system" | "light" | "dark";

interface ThemeContextType {
  theme: Theme;
  resolved: "light" | "dark";
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextType>({
  theme: "system",
  resolved: "dark",
  setTheme: () => {},
});

function useTheme() {
  return useContext(ThemeContext);
}

void useTheme;

function resolveSystemTheme(): "light" | "dark" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applySystemTheme() {
  const resolved = resolveSystemTheme();
  document.documentElement.setAttribute("data-theme", resolved);
  document.documentElement.style.colorScheme = resolved;
  return resolved;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [resolved, setResolved] = useState<"light" | "dark">(() => applySystemTheme());

  useEffect(() => {
    localStorage.removeItem("ai-platform-theme");
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => setResolved(applySystemTheme());
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  const setTheme = useCallback((t: Theme) => {
    void t;
    setResolved(applySystemTheme());
  }, []);

  return (
    <ThemeContext.Provider value={{ theme: "system", resolved, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
