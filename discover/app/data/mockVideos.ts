export interface Video {
  id: string;
  title: string;
  creator: string;
  creatorAvatar: string;
  views: string;
  viewsRaw: number; // for backend sorting representation
  publishedAt: string;
  publishedAtRaw: Date;
  duration: string;
  outlierScore: number; // e.g. 74.0 for 74x
  thumbnailUrl: string;
  category: string;
  youtubeUrl: string;
}

export const mockVideos: Video[] = [
  {
    id: "chatgpt-hacks",
    title: "5 Hacks To Use ChatGPT So Well It's Almost Unfair",
    creator: "theMITmonk",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=themitmonk",
    views: "1.6M views",
    viewsRaw: 1600000,
    publishedAt: "2mo ago",
    publishedAtRaw: new Date(Date.now() - 60 * 24 * 60 * 60 * 1000),
    duration: "16:23",
    outlierScore: 74,
    thumbnailUrl: "https://images.unsplash.com/photo-1677442136019-21780efad99a?w=600&auto=format&fit=crop&q=80",
    category: "Productivity",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "wall-staring",
    title: "i tried staring at a wall everyday for 30 days (better than adderall)",
    creator: "Mark Builds Brands",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=mark",
    views: "541K views",
    viewsRaw: 541000,
    publishedAt: "1mo ago",
    publishedAtRaw: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000),
    duration: "10:11",
    outlierScore: 300,
    thumbnailUrl: "https://images.unsplash.com/photo-1516321318423-f06f85e504b3?w=600&auto=format&fit=crop&q=80",
    category: "Productivity",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "productivity-books",
    title: "I Read 107 Productivity Books. Here's What Actually Works.",
    creator: "Ali Abdaal",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=ali",
    views: "960K views",
    viewsRaw: 960000,
    publishedAt: "24d ago",
    publishedAtRaw: new Date(Date.now() - 24 * 24 * 60 * 60 * 1000),
    duration: "18:32",
    outlierScore: 33,
    thumbnailUrl: "https://images.unsplash.com/photo-1544716278-ca5e3f4abd8c?w=600&auto=format&fit=crop&q=80",
    category: "Productivity",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "gemini-notebooklm",
    title: "This Gemini/NotebookLM System Will Make You SO Smart It Feels Illegal",
    creator: "theMITmonk",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=themitmonk",
    views: "793K views",
    viewsRaw: 793000,
    publishedAt: "1mo ago",
    publishedAtRaw: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000),
    duration: "13:18",
    outlierScore: 37,
    thumbnailUrl: "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=600&auto=format&fit=crop&q=80",
    category: "Productivity",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "claude-cowork",
    title: "Learn 80% of Claude Cowork in Under 20 Minutes",
    creator: "Jeff Su",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=jeff",
    views: "886K views",
    viewsRaw: 886000,
    publishedAt: "1mo ago",
    publishedAtRaw: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000),
    duration: "18:55",
    outlierScore: 7.36,
    thumbnailUrl: "https://images.unsplash.com/photo-1531403009284-440f080d1e12?w=600&auto=format&fit=crop&q=80",
    category: "Productivity",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "best-year-2026",
    title: "Make 2026 the Best Year of Your Life (Evidence-Based)",
    creator: "Ali Abdaal",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=ali",
    views: "541K views",
    viewsRaw: 541000,
    publishedAt: "24d ago",
    publishedAtRaw: new Date(Date.now() - 24 * 24 * 60 * 60 * 1000),
    duration: "21:42",
    outlierScore: 18,
    thumbnailUrl: "https://images.unsplash.com/photo-1506784983877-45594efa4cbe?w=600&auto=format&fit=crop&q=80",
    category: "Productivity",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "maximise-time",
    title: "maximise your time so hard that your friends will think you have 25 hours in a day",
    creator: "erin meryl study",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=erin",
    views: "289K views",
    viewsRaw: 289000,
    publishedAt: "2mo ago",
    publishedAtRaw: new Date(Date.now() - 60 * 24 * 60 * 60 * 1000),
    duration: "10:46",
    outlierScore: 40,
    thumbnailUrl: "https://images.unsplash.com/photo-1498050108023-c5249f4df085?w=600&auto=format&fit=crop&q=80",
    category: "Productivity",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "claude-hacks",
    title: "These Claude Hacks Will Make You So Productive It Feels Like Cheating",
    creator: "Dan Martell",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=dan",
    views: "418K views",
    viewsRaw: 418000,
    publishedAt: "4d ago",
    publishedAtRaw: new Date(Date.now() - 4 * 24 * 60 * 60 * 1000),
    duration: "15:13",
    outlierScore: 20,
    thumbnailUrl: "https://images.unsplash.com/photo-1555066931-4365d14bab8c?w=600&auto=format&fit=crop&q=80",
    category: "Productivity",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "hours-saved",
    title: "25h+ Saved with These Secret Automation Scripts",
    creator: "theMITmonk",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=themitmonk",
    views: "150K views",
    viewsRaw: 150000,
    publishedAt: "5d ago",
    publishedAtRaw: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000),
    duration: "15:23",
    outlierScore: 5,
    thumbnailUrl: "https://images.unsplash.com/photo-1488590528505-98d2b5aba04b?w=600&auto=format&fit=crop&q=80",
    category: "Creators",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "its-easy",
    title: "It's easy to build high performance code habits",
    creator: "Ali Abdaal",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=ali",
    views: "320K views",
    viewsRaw: 320000,
    publishedAt: "10d ago",
    publishedAtRaw: new Date(Date.now() - 10 * 24 * 60 * 60 * 1000),
    duration: "46:49",
    outlierScore: 12,
    thumbnailUrl: "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?w=600&auto=format&fit=crop&q=80",
    category: "Creators",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "done-before-10",
    title: "DONE BEFORE 10 A.M. (How I structure my hyper-productive mornings)",
    creator: "Dan Martell",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=dan",
    views: "180K views",
    viewsRaw: 180000,
    publishedAt: "12d ago",
    publishedAtRaw: new Date(Date.now() - 12 * 24 * 60 * 60 * 1000),
    duration: "15:35",
    outlierScore: 8,
    thumbnailUrl: "https://images.unsplash.com/photo-1497032628192-86f99bcd76bc?w=600&auto=format&fit=crop&q=80",
    category: "My Lists",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  },
  {
    id: "everything-by-11",
    title: "Everything Done by 11am using these 3 Google Workspace Shortcuts",
    creator: "Jeff Su",
    creatorAvatar: "https://api.dicebear.com/7.x/pixel-art/svg?seed=jeff",
    views: "120K views",
    viewsRaw: 120000,
    publishedAt: "14d ago",
    publishedAtRaw: new Date(Date.now() - 14 * 24 * 60 * 60 * 1000),
    duration: "14:14",
    outlierScore: 4.5,
    thumbnailUrl: "https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=600&auto=format&fit=crop&q=80",
    category: "My Lists",
    youtubeUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  }
];
