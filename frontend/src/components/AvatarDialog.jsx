import { useCallback, useEffect, useRef, useState } from "react";
import { getAvatarChoices, removeAvatar, setAvatar, uploadAvatar } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useToast } from "../lib/toast";
import { centered, clampOffset, cropOf, MAX_UPLOAD, MAX_ZOOM, zoomTo } from "../lib/avatar";
import { fmtShortDate } from "../lib/fmt";
import ConfirmDialog from "./ConfirmDialog";
import Icon from "./Icon";

const VIEW = 240;                                   // the crop square, px
const STEP = 12;                                    // px an arrow key moves the picture

// "2024-05-01_10-00-00" (an instaloader file name's date, UTC) as a time.
function picDate(at) {
  const m = /^(\d{4})-(\d\d)-(\d\d)_(\d\d)-(\d\d)-(\d\d)$/.exec(at || "");
  return m ? Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000 : null;
}

/* The square to keep: the picture dragged under a round window, zoomed
   with the slider, the wheel or + and -, moved with the arrow keys. Starts
   on the middle. onCrop(state) gets { w, h, view, zoom, ox, oy } (lib/avatar). */
function Cropper({ src, state, onCrop }) {
  const viewRef = useRef(null);
  const drag = useRef(null);
  const stateRef = useRef(state);
  useEffect(() => { stateRef.current = state; });

  // The wheel zooms: a listener of its own, React's is passive (the page would scroll).
  useEffect(() => {
    const el = viewRef.current;
    if (!el) return undefined;
    const wheel = e => {
      const s = stateRef.current;
      if (!s) return;
      e.preventDefault();
      onCrop(zoomTo(s, s.zoom * Math.exp(-e.deltaY * 0.0015)));
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, [onCrop]);

  function onLoad(e) {
    const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
    if (!w || !h) return;
    const s = { w, h, view: VIEW, zoom: 1 };
    onCrop({ ...s, ...centered(s) });
  }

  function move(dx, dy) {
    const s = stateRef.current;
    if (s) onCrop({ ...s, ...clampOffset({ ...s, ox: s.ox + dx, oy: s.oy + dy }) });
  }

  function onKeyDown(e) {
    const s = stateRef.current;
    if (!s) return;
    const by = { ArrowLeft: [STEP, 0], ArrowRight: [-STEP, 0], ArrowUp: [0, STEP], ArrowDown: [0, -STEP] }[e.key];
    if (by) { e.preventDefault(); move(...by); }
    else if (e.key === "+" || e.key === "=") { e.preventDefault(); onCrop(zoomTo(s, s.zoom * 1.15)); }
    else if (e.key === "-") { e.preventDefault(); onCrop(zoomTo(s, s.zoom / 1.15)); }
  }

  const scale = state ? (VIEW / Math.min(state.w, state.h)) * state.zoom : 0;
  return (
    <div className="avatar-crop">
      <div
        ref={viewRef}
        className={`avatar-crop-view${state ? "" : " is-loading"}`}
        style={{ width: VIEW, height: VIEW }}
        tabIndex={0}
        role="group"
        aria-label="Crop: drag the picture, or use the arrow keys; + and - zoom"
        onKeyDown={onKeyDown}
        onPointerDown={e => {
          if (!state) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { x: e.clientX, y: e.clientY };
        }}
        onPointerMove={e => {
          if (!drag.current) return;
          const { x, y } = drag.current;
          drag.current = { x: e.clientX, y: e.clientY };
          move(e.clientX - x, e.clientY - y);
        }}
        onPointerUp={() => { drag.current = null; }}
        onPointerCancel={() => { drag.current = null; }}
      >
        <img
          src={src}
          alt=""
          draggable={false}
          onLoad={onLoad}
          style={state ? { left: state.ox, top: state.oy, width: state.w * scale, height: state.h * scale }
                       : { visibility: "hidden" }}
        />
        <span className="avatar-crop-ring" aria-hidden="true" />
      </div>
      <label className="avatar-crop-zoom">
        <span>Zoom</span>
        <input type="range" min={1} max={MAX_ZOOM} step={0.01} value={state?.zoom ?? 1} disabled={!state}
               onChange={e => state && onCrop(zoomTo(state, Number(e.target.value)))} />
      </label>
    </div>
  );
}

/* "Change picture" on a person's page (docs/API.md "Profile pictures"): a
   picture uploaded (picked, dropped or pasted), one of their posts', or the
   profile picture instaloader saved for them (one click); the first two
   cropped square here. Remove takes theirs away. Mount it when it opens.

   Props: person, onChanged(), onClose */
export default function AvatarDialog({ person: p, onChanged, onClose }) {
  const toast = useToast();
  const load = useCallback(() => getAvatarChoices(p.id), [p.id]);
  const choices = useApi(load, 0);
  const [picked, setPicked] = useState(null);       // { src, file } or { src, media }
  const [crop,   setCrop]   = useState(null);       // the Cropper's state, once the picture loaded
  const [busy,   setBusy]   = useState(false);
  const [error,  setError]  = useState(null);
  const [over,   setOver]   = useState(false);      // a file dragged over the drop zone
  const fileRef = useRef(null);                    // the Choose a file button
  const inputRef = useRef(null);

  // An uploaded file's preview URL goes with it.
  useEffect(() => {
    const src = picked?.file ? picked.src : null;
    return () => { if (src) URL.revokeObjectURL(src); };
  }, [picked]);

  const pickFile = useCallback(file => {
    if (!file) return;
    if (!file.type.startsWith("image/")) { setError("That is not a picture."); return; }
    if (file.size > MAX_UPLOAD) { setError(`That picture is too large: at most ${MAX_UPLOAD / 1024 / 1024} MB.`); return; }
    setError(null);
    setCrop(null);
    setPicked({ src: URL.createObjectURL(file), file });
  }, []);

  // A picture pasted anywhere while the dialog is open.
  useEffect(() => {
    const paste = e => {
      const file = [...(e.clipboardData?.files || [])].find(f => f.type.startsWith("image/"));
      if (file) { e.preventDefault(); pickFile(file); }
    };
    document.addEventListener("paste", paste);
    return () => document.removeEventListener("paste", paste);
  }, [pickFile]);

  async function run(fn, done) {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      if (!r?.ok) { setError(r?.error || "Could not save the picture."); return; }
      toast(done);
      onChanged();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const save = () => {
    if (!picked || !crop) return;
    const box = cropOf(crop);
    run(() => (picked.file ? uploadAvatar(p.id, picked.file, box) : setAvatar(p.id, { media: picked.media }, box)),
        "Picture saved.");
  };

  const pics = choices.data?.profile_pics || [];
  const media = choices.data?.media || [];
  return (
    <ConfirmDialog
      open
      title={`${p.name}'s picture`}
      confirmLabel="Save picture"
      confirmDisabled={!picked || !crop}
      busy={busy}
      error={error}
      onConfirm={save}
      onCancel={onClose}
      initialFocus={fileRef}
    >
      {picked ? (
        <div className="avatar-pick">
          <Cropper src={picked.src} state={crop} onCrop={setCrop} />
          <button type="button" className="btn-ghost" onClick={() => { setPicked(null); setCrop(null); }}>
            <Icon name="back" size={14} />Choose another
          </button>
        </div>
      ) : (
        <div className="avatar-pick">
          <div
            className={`avatar-drop${over ? " is-over" : ""}`}
            onDragOver={e => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={e => { e.preventDefault(); setOver(false); pickFile(e.dataTransfer.files?.[0]); }}
          >
            <Icon name="image" size={18} />
            <span>Drop a picture here, or paste one</span>
            <button ref={fileRef} type="button" className="btn-secondary" onClick={() => inputRef.current?.click()}>
              Choose a file…
            </button>
            <input ref={inputRef} type="file" accept="image/*" hidden aria-label="Picture file"
                   onChange={e => { pickFile(e.target.files?.[0]); e.target.value = ""; }} />
          </div>

          {pics.length > 0 && (
            <div className="avatar-source">
              <h4 className="link-group-title">Profile picture saved by instaloader</h4>
              <div className="avatar-profile-pics">
                {pics.map((f, i) => (
                  <button key={f.key} type="button" className={`avatar-profile-pic${i === 0 ? " is-newest" : ""}`}
                          disabled={busy}
                          title={`${f.name}${f.account?.handle ? ` (@${f.account.handle})` : ""}`}
                          onClick={() => run(() => setAvatar(p.id, { profile_pic: f.key }), "Profile picture set.")}>
                    <img src={f.url} alt="" loading="lazy" />
                    <span>{i === 0 ? "Use the newest" : "Use this one"}
                      {picDate(f.at) && <span className="creator-sub"> · {fmtShortDate(picDate(f.at))}</span>}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="avatar-source">
            <h4 className="link-group-title">From their posts</h4>
            {choices.error ? <div className="empty">Could not load: {choices.error.message}</div>
              : !choices.data ? <div className="empty">Loading…</div>
              : media.length === 0 ? <div className="empty">No picture among their posts.</div>
              : (
                <div className="avatar-media-grid">
                  {media.map(m => (
                    <button key={m.id} type="button" className="avatar-media" title={`From ${m.post}`}
                            aria-label={`A ${m.kind === "video" ? "video's poster" : "picture"} from ${m.post}`}
                            onClick={() => { setError(null); setCrop(null); setPicked({ src: m.thumb, media: m.id }); }}>
                      <img src={m.thumb} alt="" loading="lazy" />
                      {m.kind === "video" && <Icon name="play" size={12} />}
                    </button>
                  ))}
                </div>
              )}
          </div>

          {p.avatar && (
            <button type="button" className="btn-ghost person-delete avatar-remove" disabled={busy}
                    onClick={() => run(() => removeAvatar(p.id), "Picture removed.")}>
              <Icon name="trash" size={14} />Remove picture
            </button>
          )}
        </div>
      )}
    </ConfirmDialog>
  );
}
