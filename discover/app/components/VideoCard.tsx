"use client";

import React from "react";
import { Video } from "../data/mockVideos";

interface VideoCardProps {
  video: Video;
}

export default function VideoCard({ video }: VideoCardProps) {
  // Format dynamic HSL colors for the outlier badge based on multiplier
  const getOutlierBadgeStyles = (score: number) => {
    if (score >= 100) {
      return {
        background: "rgba(220, 38, 38, 0.15)",
        color: "#DC2626",
        border: "1px solid rgba(220, 38, 38, 0.3)",
        label: "Extreme Outlier"
      };
    } else if (score >= 30) {
      return {
        background: "rgba(217, 119, 6, 0.15)",
        color: "#D97706",
        border: "1px solid rgba(217, 119, 6, 0.3)",
        label: "High Outlier"
      };
    } else if (score >= 10) {
      return {
        background: "rgba(245, 158, 11, 0.12)",
        color: "#F59E0B",
        border: "1px solid rgba(245, 158, 11, 0.25)",
        label: "Mid Outlier"
      };
    } else {
      return {
        background: "var(--color-surface-raised)",
        color: "var(--color-text-secondary)",
        border: "1px solid var(--color-border-subtle)",
        label: "Low Outlier"
      };
    }
  };

  const badgeStyle = getOutlierBadgeStyles(video.outlierScore);

  return (
    <article className="group/card flex flex-col bg-transparent rounded-md overflow-hidden relative transition-all duration-250 ease-in-out hover:-translate-y-1" id={`video-card-${video.id}`}>
      {/* Thumbnail Wrap */}
      <a href={video.youtubeUrl} target="_blank" rel="noopener noreferrer" className="relative block w-full aspect-video rounded-md overflow-hidden border border-border-subtle bg-surface-raised">
        <img
          src={video.thumbnailUrl}
          alt={video.title}
          referrerPolicy="no-referrer"
          className="w-full h-full object-cover transition-transform duration-250 ease-in-out group-hover/card:scale-[1.04]"
          loading="lazy"
        />
        <span className="absolute bottom-2 right-2 bg-black/82 text-white text-[0.72rem] font-semibold py-0.5 px-1.5 rounded tracking-[0.02em]">{video.duration}</span>
      </a>

      {/* Video Info / Details */}
      <div className="flex pt-3 pb-2 px-0.5 gap-3 relative">
        {/* Profile Avatar */}
        <div className="shrink-0">
          <img
            src={video.creatorAvatar}
            alt={video.creator}
            referrerPolicy="no-referrer"
            className="w-9 h-9 rounded-full bg-surface-raised border border-border-subtle object-cover"
          />
        </div>

        {/* Text Metadata */}
        <div className="flex-1 flex flex-col gap-1 min-w-0">
          <h3 className="text-[0.95rem] font-semibold leading-[1.35] text-primary line-clamp-2 overflow-hidden text-ellipsis max-h-[2.7em] tracking-[-0.01em] sm:text-[0.95rem] text-[0.9rem]" title={video.title}>
            <a href={video.youtubeUrl} target="_blank" rel="noopener noreferrer" className="hover:text-brand transition-colors duration-150">
              {video.title}
            </a>
          </h3>
          
          <span className="text-[0.82rem] font-medium text-secondary">{video.creator}</span>
          
          <div className="flex items-center justify-between mt-1 gap-2">
            <div className="flex items-center text-[0.8rem] text-secondary whitespace-nowrap sm:text-[0.8rem] text-[0.78rem]">
              <span>{video.views}</span>
              <span className="opacity-50 mx-1">•</span>
              <span>{video.publishedAt}</span>
            </div>

            {/* Dynamic Outlier Score Badge */}
            <div
              className="text-[0.78rem] font-bold py-[3px] px-2 rounded-full border border-transparent inline-flex items-center justify-center transition-all duration-150 whitespace-nowrap"
              style={{
                backgroundColor: badgeStyle.background,
                color: badgeStyle.color,
                borderColor: badgeStyle.color
              }}
              title={`${badgeStyle.label}: ${video.outlierScore}x typical view count`}
            >
              {video.outlierScore}x
            </div>
          </div>
        </div>

        {/* YouTube Watch Trigger Icon */}
        <a
          href={video.youtubeUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="absolute bottom-2 right-0.5 text-disabled flex items-center justify-center w-7 h-7 rounded-full bg-transparent transition-all duration-150 opacity-80 sm:opacity-0 sm:group-hover/card:opacity-100 hover:bg-surface-raised hover:text-[#FF0000] dark:hover:text-[#FF4D4D]"
          title="Watch on YouTube"
        >
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M23.498 6.163a3.003 3.003 0 0 0-2.11-2.107C19.528 3.545 12 3.545 12 3.545s-7.528 0-9.388.511a3.002 3.002 0 0 0-2.11 2.107C0 8.021 0 12 0 12s0 3.979.502 5.837a3.002 3.002 0 0 0 2.11 2.107c1.86.511 9.388.511 9.388.511s7.528 0 9.388-.511a3.002 3.002 0 0 0 2.11-2.107c.502-1.858.502-5.837.502-5.837s0-3.979-.502-5.837zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
          </svg>
        </a>
      </div>
    </article>
  );
}

