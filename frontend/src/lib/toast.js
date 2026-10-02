import { createContext, useContext } from "react";

/* App-wide toasts. Lives above the router outlet so a message survives the
   navigation that usually follows it (e.g. deleting the post you are on). */
export const ToastContext = createContext(() => {});

// toast(text, kind = "ok" | "err", link = { to, label } | undefined)
export function useToast() {
  return useContext(ToastContext);
}
