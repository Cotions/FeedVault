import { getBrowserProfiles } from "../lib/api";
import { useApi } from "../lib/useApi";
import { BROWSERS, profileChoices, profileMissing } from "../lib/browserProfiles";

/* The browser a cookies session reads, and which of its profiles: "Default"
   leaves that to the tool; the names come from the browser itself (GET
   /api/browsers/profiles: Firefox's profiles.ini, the others' folders). A
   stored profile the browser no longer has stays shown, marked: FeedVault
   refuses its syncs sooner than use another profile's cookies.
   ``className``: the selects' own (the form's input style). */
export default function BrowserPick({ browser, profile, onChange, className = "insta-input", disabled = false }) {
  const { data, error } = useApi(getBrowserProfiles);
  const listed = data?.profiles ? data.profiles[browser] || [] : null;
  return (
    <div className="browser-pick">
      <select className={className} aria-label="Browser" value={browser} disabled={disabled}
              onChange={e => onChange({ browser: e.target.value, profile: "" })}>
        {BROWSERS.map(b => <option key={b} value={b}>{b}</option>)}
      </select>
      <label className="browser-pick-profile">
        <span className="dim">Browser profile</span>
        <select className={className} aria-label="Browser profile" value={profile} disabled={disabled}
                onChange={e => onChange({ browser, profile: e.target.value })}>
          {profileChoices(listed, profile).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      </label>
      {profileMissing(listed, profile) && (
        <span className="dl-warn browser-pick-note" role="note">
          {browser} has no profile named {profile} now (renamed or removed?): syncs are refused until you pick another.
        </span>
      )}
      {error && !data && <span className="dim browser-pick-note">Could not list the profiles: {error.message}</span>}
    </div>
  );
}
