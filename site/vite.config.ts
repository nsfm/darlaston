import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served from https://nsfm.github.io/darlaston/ unless a custom domain
// takes over, in which case set SITE_BASE=/ in the workflow.
export default defineConfig({
  base: process.env.SITE_BASE ?? "/darlaston/",
  plugins: [react()],
});
