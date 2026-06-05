"use client";

import React, { useState, useEffect, useRef } from "react";

// Easily tweakable constants
const DEBOUNCE_DELAY_MS = 300;
const API_BASE_URL = "http://localhost:8000";

interface SearchBarProps {
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  onOpenFilters: () => void;
  activePlatform: string;
  activeTimeRange: string;
  activeMinOutlier: number;
}

interface Creator {
  channel_id: string;
  name: string;
  thumbnail_url: string;
  description: string;
}

export default function SearchBar({
  searchQuery,
  setSearchQuery,
  onOpenFilters,
  activePlatform,
  activeTimeRange,
  activeMinOutlier,
}: SearchBarProps) {
  const [creatorResults, setCreatorResults] = useState<Creator[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside of the search component
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  // Debounced Creator Search API Query
  useEffect(() => {
    const trimmed = searchQuery.trim();
    if (!trimmed) {
      setCreatorResults([]);
      setIsLoading(false);
      setShowDropdown(false);
      return;
    }

    setIsLoading(true);
    setShowDropdown(true);

    const timer = setTimeout(async () => {
      try {
        const response = await fetch(
          `${API_BASE_URL}/api/youtube/search-creators?q=${encodeURIComponent(trimmed)}`
        );
        if (response.ok) {
          const data = await response.json();
          setCreatorResults(data.results || []);
        } else {
          console.error("Failed to fetch creator list from backend API");
          setCreatorResults([]);
        }
      } catch (error) {
        console.error("Network error during creator search:", error);
        setCreatorResults([]);
      } finally {
        setIsLoading(false);
      }
    }, DEBOUNCE_DELAY_MS);

    return () => {
      clearTimeout(timer);
    };
  }, [searchQuery]);

  // Format the time range label elegantly
  const formatTimeRange = (range: string) => {
    switch (range) {
      case "weekly": return "Last week";
      case "monthly": return "Last month";
      case "3months": return "Last 3 months";
      case "6months": return "Last 6 months";
      default: return "All time";
    }
  };

  return (
    <div ref={containerRef} className="w-full mt-6 mb-4 relative z-50">
      {/* Input Outer Pill Container */}
      <div className="relative flex items-center w-full bg-surface border-[1.5px] border-border-subtle rounded-full pr-1.5 pl-4 sm:pl-4 pl-3 py-1.5 shadow-sm transition-all duration-150 ease-in-out focus-within:border-brand focus-within:ring-3 focus-within:ring-brand-subtle/30">
        <div className="flex items-center justify-center text-secondary pointer-events-none mr-3 shrink-0">
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
        </div>
        <input
          type="text"
          id="search-input-field"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onFocus={() => {
            if (searchQuery.trim()) {
              setShowDropdown(true);
            }
          }}
          placeholder="Search videos, creators or lists..."
          className="flex-1 bg-transparent border-none py-2 px-0 text-base text-primary w-full placeholder-disabled sm:text-base text-[0.95rem] outline-none focus:outline-none"
        />
        <button
          id="btn-open-filter-modal"
          onClick={onOpenFilters}
          className="flex items-center bg-surface-raised border border-border-subtle text-secondary rounded-full py-2 px-4 text-[0.85rem] font-medium gap-2 transition-all duration-150 ease-in-out whitespace-nowrap ml-3 sm:py-2 sm:px-4 sm:text-[0.85rem] sm:gap-2 sm:ml-3 py-1.5 px-2.5 text-[0.75rem] gap-1 ml-1.5 hover:bg-surface-overlay hover:text-primary hover:border-border shrink-0"
          title="Configure Outlier Filters"
        >
          <span className="text-[#FF0000] dark:text-[#FF4D4D] flex items-center shrink-0">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
              <path d="M23.498 6.163a3.003 3.003 0 0 0-2.11-2.107C19.528 3.545 12 3.545 12 3.545s-7.528 0-9.388.511a3.002 3.002 0 0 0-2.11 2.107C0 8.021 0 12 0 12s0 3.979.502 5.837a3.002 3.002 0 0 0 2.11 2.107c1.86.511 9.388.511 9.388.511s7.528 0 9.388-.511a3.002 3.002 0 0 0 2.11-2.107c.502-1.858.502-5.837.502-5.837s0-3.979-.502-5.837zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
            </svg>
          </span>
          <span className="sm:max-w-none max-w-[90px] overflow-hidden text-ellipsis">
            {activePlatform} <span className="opacity-40 mx-0.5 sm:inline-block hidden">•</span> {formatTimeRange(activeTimeRange)} <span className="opacity-40 mx-0.5 sm:inline-block hidden">•</span> {activeMinOutlier}x
          </span>
          <span className="flex items-center opacity-70 shrink-0">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
          </span>
        </button>
      </div>

      {/* Creator Search Results Dropdown List */}
      {showDropdown && (
        <div className="absolute left-0 right-0 top-full mt-2 bg-surface border border-border-subtle rounded-2xl shadow-lg max-h-[320px] overflow-y-auto animate-fade-in z-50">
          {isLoading && (
            <div className="p-5 text-center text-secondary flex items-center justify-center gap-2">
              <svg className="animate-spin h-5 w-5 text-brand shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
              </svg>
              <span className="text-[0.95rem] font-medium">Searching creators on YouTube...</span>
            </div>
          )}

          {!isLoading && creatorResults.length === 0 && (
            <div className="p-6 text-center text-disabled text-[0.95rem] font-medium">
              No matching creators found on YouTube for "{searchQuery}"
            </div>
          )}

          {!isLoading && creatorResults.length > 0 && (
            <div className="py-2.5">
              <div className="px-4 pb-2 pt-1 text-[0.78rem] font-bold text-disabled uppercase tracking-wider select-none border-b border-border-subtle">
                YouTube Creator Results
              </div>
              {creatorResults.map((creator) => (
                <button
                  key={creator.channel_id}
                  onClick={() => {
                    setSearchQuery(creator.name);
                    setShowDropdown(false);
                  }}
                  className="w-full text-left px-4 py-3 hover:bg-surface-raised active:bg-surface-overlay transition-colors duration-150 flex items-center gap-3 border-b border-border-subtle last:border-0"
                >
                  {creator.thumbnail_url ? (
                    <img
                      src={creator.thumbnail_url}
                      alt={creator.name}
                      referrerPolicy="no-referrer"
                      className="w-10 h-10 rounded-full object-cover border border-border-subtle shrink-0"
                    />
                  ) : (
                    <div className="w-10 h-10 rounded-full bg-surface-raised flex items-center justify-center text-secondary font-bold shrink-0 border border-border-subtle">
                      {creator.name.slice(0, 1).toUpperCase()}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="text-primary font-bold truncate text-[0.95rem]">
                      {creator.name}
                    </div>
                    {creator.description && (
                      <div className="text-secondary text-[0.82rem] truncate mt-0.5 max-w-full">
                        {creator.description}
                      </div>
                    )}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
