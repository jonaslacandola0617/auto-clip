import { useEffect, useRef } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";

export function VideoPreview({ path, start = 0, end, vertical = false }: { path: string; start?: number; end?: number; vertical?: boolean }) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (video && Number.isFinite(start)) video.currentTime = start;
  }, [path, start]);

  return (
    <video
      ref={videoRef}
      className={vertical ? "video-preview video-preview--vertical" : "video-preview"}
      src={convertFileSrc(path)}
      controls
      preload="metadata"
      onLoadedMetadata={(event) => { event.currentTarget.currentTime = start; }}
      onTimeUpdate={(event) => {
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
