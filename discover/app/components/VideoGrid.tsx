"use client";

import React from "react";
import VideoCard from "./VideoCard";
import { Video } from "../data/mockVideos";

interface VideoGridProps {
  videos: Video[];
  onResetFilters?: () => void;
}

export default function VideoGrid({ videos, onResetFilters }: VideoGridProps) {
  if (videos.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center sm:py-16 sm:px-6 py-10 px-4 w-full bg-surface border-[1.5px] border-dashed border-border rounded-lg my-6 mb-12 shadow-sm animate-fade-in">
        <div className="text-disabled mb-4 flex items-center justify-center">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="8" y1="12" x2="16" y2="12"></line>
          </svg>
        </div>
        <h3 className="text-xl font-bold text-primary mb-2 tracking-[-0.01em]">No Outliers Found</h3>
        <p className="text-[0.9rem] text-secondary max-w-[440px] leading-relaxed mb-6">
          No videos match your active search term or filter configuration. Try lowering the outlier score threshold or clearing the search query.
        </p>
        {onResetFilters && (
          <button
            id="btn-reset-filters-empty"
            onClick={onResetFilters}
            className="py-2.5 px-6 bg-primary text-bg rounded-md text-[0.88rem] font-semibold transition-all duration-150 ease-in-out shadow-sm dark:bg-surface-overlay dark:text-primary hover:bg-brand hover:text-on-brand hover:-translate-y-[1px] hover:shadow-md active:translate-y-0"
          >
            Reset Filters
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="grid sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))] grid-cols-1 gap-x-5 sm:gap-y-8 gap-y-6 w-full mb-12">
      {videos.map((video) => (
        <VideoCard key={video.id} video={video} />
      ))}
    </div>
  );
}

