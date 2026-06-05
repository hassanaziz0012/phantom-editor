import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Phantom Discover - Outlier Analysis Dashboard",
  description: "Discover viral video trends, identify high-performing content outliers, and analyze channel performance with premium metric tracking.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="max-w-[100vw] overflow-x-hidden text-[16px] antialiased">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Google+Sans+Flex:wght@100..900&display=swap" rel="stylesheet" />
      </head>
      <body className="bg-bg text-primary font-sans leading-normal transition-colors duration-[250ms]">
        {children}
      </body>
    </html>
  );
}
