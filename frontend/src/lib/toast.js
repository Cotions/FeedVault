import { createContext, useContext } from "react";

/* App-wide toasts. Lives above the router outlet so a message survives the
   navigation that usually follows it (e.g. deleting the post you are on). */
export const ToastContext = createContext(() => {});

// toast(text, kind = "ok" | "err" | "info", link = { to, label } | { onClick, label } (an action: Undo) | undefined)
// A toast with an action shows longest, and none goes while hovered or
// focused (App.jsx's Toast). An onClick that returns a promise keeps its
// toast, the button busy, until it settles: say the outcome in a toast.
export function useToast() {
  return useContext(ToastContext);
}
