"use client";

import React, { useState } from "react";

interface CategoryPillsProps {
  selectedCategory: string;
  setSelectedCategory: (cat: string) => void;
  onRefresh: () => void;
}

export default function CategoryPills({
  selectedCategory,
  setSelectedCategory,
  onRefresh,
}: CategoryPillsProps) {
  const [categories, setCategories] = useState(["All", "Productivity"]);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const handleAddCategory = () => {
    const newCat = prompt("Enter a new category name:");
    if (newCat && newCat.trim() !== "") {
      const cleanCat = newCat.trim();
      if (!categories.includes(cleanCat)) {
        setCategories([...categories, cleanCat]);
        setSelectedCategory(cleanCat);
      }
    }
  };

  const handleRefreshClick = () => {
    setIsRefreshing(true);
    onRefresh();
    setTimeout(() => setIsRefreshing(false), 800);
  };

  return (
    <div className="flex items-center justify-between w-full mt-4 mb-6 gap-4 flex-wrap sm:flex-row flex-col sm:items-center items-start">
      {/* Category Pills (Left Side) */}
      <div className="flex items-center gap-2.5 flex-wrap">
        {categories.map((cat) => {
          const isActive = selectedCategory === cat;
          const displayLabel = cat === "Productivity" ? "🔥 Productivity" : cat;
          return (
            <button
              key={cat}
              id={`btn-category-${cat.toLowerCase()}`}
              onClick={() => setSelectedCategory(cat)}
              className={`py-2 px-4 text-[0.88rem] rounded-sm border transition-all duration-150 ease-in-out flex items-center gap-1.5 ${
                isActive
                  ? "bg-primary text-bg border-primary font-semibold dark:bg-surface-overlay dark:text-primary dark:border-primary"
                  : "bg-surface border-border-subtle text-secondary font-medium hover:bg-surface-raised hover:text-primary hover:border-border"
              }`}
            >
              {displayLabel}
            </button>
          );
        })}
        <button
          id="btn-add-category"
          onClick={handleAddCategory}
          className="py-2 px-3.5 text-[0.88rem] font-medium rounded-sm bg-transparent border border-dashed border-border text-secondary transition-all duration-150 flex items-center gap-1 hover:border-secondary hover:text-primary hover:bg-black/[0.02] dark:hover:bg-white/[0.03]"
        >
          <span className="opacity-70 text-base">+</span> Add
        </button>
      </div>

      {/* Action Controls (Right Side) */}
      <div className="flex items-center gap-2 sm:w-auto sm:justify-start sm:border-t-0 sm:pt-0 w-full justify-end border-t border-border-subtle pt-2.5">
        {/* Hide/Show Toggle Action */}
        <button
          className="flex items-center justify-center w-9 h-9 rounded-full text-secondary bg-transparent border border-transparent transition-all duration-150 hover:bg-surface-raised hover:text-primary hover:border-border-subtle"
          title="Hide Unselected Elements"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
            <line x1="1" y1="1" x2="23" y2="23"></line>
          </svg>
        </button>
        {/* View Mode Toggle Action */}
        <button
          className="flex items-center justify-center w-9 h-9 rounded-full bg-surface-raised text-primary border border-border-subtle transition-all duration-150"
          title="Grid View Mode"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="7" height="9"></rect>
            <rect x="14" y="3" width="7" height="5"></rect>
            <rect x="14" y="12" width="7" height="9"></rect>
            <rect x="3" y="16" width="7" height="5"></rect>
          </svg>
        </button>
        {/* Reload / Sync Action */}
        <button
          onClick={handleRefreshClick}
          className="flex items-center justify-center w-9 h-9 rounded-full text-secondary bg-transparent border border-transparent transition-all duration-150 hover:bg-surface-raised hover:text-primary hover:border-border-subtle"
          title="Refresh Video Data"
        >
          <svg
            className={isRefreshing ? "animate-[spin_0.8s_linear_infinite]" : ""}
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <polyline points="23 4 23 10 17 10"></polyline>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
          </svg>
        </button>
      </div>
    </div>
  );
}

