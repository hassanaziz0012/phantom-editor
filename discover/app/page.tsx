"use client";

import React, { useState, useMemo } from "react";
import TabNavigation from "./components/TabNavigation";
import SearchBar from "./components/SearchBar";
import VideoGrid from "./components/VideoGrid";
import FilterModal from "./components/FilterModal";
import { mockVideos } from "./data/mockVideos";

export default function Home() {
  // Navigation & Filtering States
  const [activeTab, setActiveTab] = useState("discover");
  const [searchQuery, setSearchQuery] = useState("");
  const [isFilterModalOpen, setIsFilterModalOpen] = useState(false);

  // Configuration Filter States (Syncs to FilterModal)
  const [platform, setPlatform] = useState("YouTube");
  const [timeRange, setTimeRange] = useState("3months");
  const [minOutlier, setMinOutlier] = useState(10); // default matching screenshot (10x)
  const [sortBy, setSortBy] = useState("outlierScore");

  // Reset all filters to default
  const handleResetFilters = () => {
    setSearchQuery("");
    setPlatform("YouTube");
    setTimeRange("all");
    setMinOutlier(1.5);
    setSortBy("outlierScore");
  };

  // High-fidelity search and filter sorting pipeline
  const filteredVideos = useMemo(() => {
    let result = [...mockVideos];

    // 1. Filter by Active Tab
    if (activeTab === "creators") {
      // Creator tab highlights curated creator-driven items
      result = result.filter(v => v.category === "Creators" || v.creator === "Ali Abdaal" || v.creator === "theMITmonk");
    } else if (activeTab === "mylists") {
      // User lists filters down to saved bookmark lists
      result = result.filter(v => v.category === "My Lists");
    }
    // (Discover tab allows viewing all items across all categories)

    // 2. Filter by Search Query (title or creator)
    if (searchQuery.trim() !== "") {
      const q = searchQuery.toLowerCase().trim();
      result = result.filter(
        v => v.title.toLowerCase().includes(q) || v.creator.toLowerCase().includes(q)
      );
    }

    // 3. Filter by Minimum Outlier Multiplier
    result = result.filter(v => v.outlierScore >= minOutlier);

    // 4. Filter by Publish Time Range Cutoff
    if (timeRange !== "all") {
      const now = new Date();
      result = result.filter(v => {
        const diffMs = now.getTime() - v.publishedAtRaw.getTime();
        const diffDays = diffMs / (1000 * 60 * 60 * 24);

        if (timeRange === "weekly") return diffDays <= 7;
        if (timeRange === "monthly") return diffDays <= 30;
        if (timeRange === "3months") return diffDays <= 90;
        if (timeRange === "6months") return diffDays <= 180;
        return true;
      });
    }

    // 5. Sort matching items
    result.sort((a, b) => {
      if (sortBy === "views") {
        return b.viewsRaw - a.viewsRaw;
      }
      if (sortBy === "newest") {
        return b.publishedAtRaw.getTime() - a.publishedAtRaw.getTime();
      }
      // Default: sort by outlier multiplier (highest first)
      return b.outlierScore - a.outlierScore;
    });

    return result;
  }, [activeTab, searchQuery, minOutlier, timeRange, sortBy]);

  return (
    <div className="w-full max-w-[1280px] mx-auto px-4 sm:px-6 min-h-screen flex flex-col">
      {/* Search Input Bar (Top Section) */}
      <SearchBar
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        onOpenFilters={() => setIsFilterModalOpen(true)}
        activePlatform={platform}
        activeTimeRange={timeRange}
        activeMinOutlier={minOutlier}
      />

      {/* Tab Horizontal Navigation (Discover, Creators, My Lists) */}
      <TabNavigation activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* Categories & Actions section removed */}

      {/* Main Grid display of matching video cards */}
      <main className="flex-1 flex flex-col">
        <VideoGrid
          videos={filteredVideos}
          onResetFilters={handleResetFilters}
        />
      </main>

      {/* Filter Options Configuration Overlay Modal */}
      <FilterModal
        isOpen={isFilterModalOpen}
        onClose={() => setIsFilterModalOpen(false)}
        platform={platform}
        setPlatform={setPlatform}
        timeRange={timeRange}
        setTimeRange={setTimeRange}
        minOutlier={minOutlier}
        setMinOutlier={setMinOutlier}
        sortBy={sortBy}
        setSortBy={setSortBy}
      />
    </div>
  );
}
