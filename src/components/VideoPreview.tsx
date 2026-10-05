import { useEffect, useRef, useState } from "react";
import { convertFileSrc, invoke } from "@tauri-apps/api/core";

export function VideoPreview({ path, start = 0, end, vertical = false, seekTo, seekKey = 0, onTimeUpdate }: {
  path: string;
  start?: number;
  end?: number;
  vertical?: boolean;
  /** Bump this to a new timestamp (e.g. from a transcript timecode click) to seek and resume playback. */
  seekTo?: number;
  seekKey?: number;
  /** Reports the current playhead position, e.g. so a transcript can highlight the segment being played. */
  onTimeUpdate?: (seconds: number) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [source, setSource] = useState("");

  useEffect(() => {
    let current = true;
    setSource("");
    invoke<string>("allow_media_path", { path })
      .then((allowedPath) => { if (current) setSource(convertFileSrc(allowedPath)); })
      .catch(() => { if (current) setSource(""); });
    return () => { current = false; };
  }, [path]);

  useEffect(() => {
    const video = videoRef.current;
    if (video && Number.isFinite(start)) video.currentTime = start;
  }, [path, start]);

  useEffect(() => {
    const video = videoRef.current;
    if (video && seekTo !== undefined) { video.currentTime = seekTo; void video.play().catch(() => {}); }
  }, [seekTo, seekKey]);

  return (
    <video
      ref={videoRef}
      className={vertical ? "video-preview video-preview--vertical" : "video-preview"}
      src={source || undefined}
      controls
      preload="metadata"
      onLoadedMetadata={(event) => { event.currentTarget.currentTime = start; }}
      onTimeUpdate={(event) => {
        onTimeUpdate?.(event.currentTarget.currentTime);
        if (end !== undefined && event.currentTarget.currentTime >= end) {
          event.currentTarget.pause();
          event.currentTarget.currentTime = start;
        }
      }}
    >
      This video codec cannot be previewed directly. Generate a preview proxy instead.
    </video>
  );
}
