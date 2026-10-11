import { createContext, useContext } from "react";
import { activateProfile } from "./api";

/* Profiles: separate libraries, one active at a time (docs/API.md
   "Profiles"). Loaded once by App, shared by the header's switcher and
   Settings → Profiles.

   { profiles, reload() }
   - profiles: GET /api/profiles' list ([] until it answers)
   - reload:   read it again (after a create, a rename, a remove) */
export const ProfilesContext = createContext({ profiles: [], reload: () => {} });

export function useProfiles() {
  return useContext(ProfilesContext);
}

/* Every page holds the old library's data (posts, people, ids in its URL),
   so a switch starts the whole app again from the feed rather than patch
   what is on screen. Throws with the backend's reason when it refuses (a
   job or a scan is running). */
export async function switchProfile(id, restart = () => window.location.assign("/")) {
  const r = await activateProfile(id);
  if (!r?.ok) throw new Error(r?.error || "Could not switch profile.");
  restart();
}

// "1,204 posts", or "" for a library that could not be read.
export function profileCount(p) {
  if (p?.posts == null) return "";
  return `${p.posts.toLocaleString("en-US")} post${p.posts === 1 ? "" : "s"}`;
}
