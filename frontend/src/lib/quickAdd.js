import { createContext, useContext } from "react";

/* The app's quick-add dialog (App.jsx: the header's "+ Link", Alt+L, a
   dropped address), for a page to open as its own "Add link" (the Links
   page): one form to add a link, wherever it is asked for.
   open(url = "", { download = false }): the address and the Download
   checkbox it starts with. What is saved comes back through the app (a
   toast, and the page's links read again). */
export const QuickAddContext = createContext(() => {});

export function useQuickAdd() {
  return useContext(QuickAddContext);
}
