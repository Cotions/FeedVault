import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

/* A <video> that lets go of its file when it leaves the page. A removed
   <video> keeps its decoder, its buffered data and its connection until the
   garbage collector gets to it, which in a long session (Review's next post,
   its preloads, a carousel, the feed's covers) can be many of them at once.
   Emptying its src and calling load() frees them now. Only once it is out of
   the document: StrictMode's rehearsal unmount leaves it in place, and a
   video still shown must keep playing. Props and ref go to the <video>. */
function releaseVideo(v) {
  if (!v || v.isConnected) return;
  v.pause();
  v.removeAttribute("src");
  v.load();
}

const Video = forwardRef(function Video(props, ref) {
  const own = useRef(null);
  useImperativeHandle(ref, () => own.current, []);
  useEffect(() => {
    const v = own.current;
    return () => releaseVideo(v);
  }, []);
  return <video ref={own} {...props} />;
});

export default Video;
