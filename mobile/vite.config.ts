import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" so the same build works at /app/ on the API server and inside the Android / iOS shells
export default defineConfig({ plugins: [react()], base: "./", build: { outDir: "dist", sourcemap: false } });
