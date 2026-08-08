import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base, so the same build serves from nsfm.github.io/darlaston/
// and from darlaston.app without caring which. Safe because the site is a
// single page with anchor links; introduce real routing and this needs to
// become an absolute base again.
export default defineConfig({
  base: "./",
  plugins: [react()],
});
