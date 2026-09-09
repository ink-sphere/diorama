import type { NextConfig } from "next";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const config: NextConfig = {
  turbopack: { root },
  serverExternalPackages: ["jsdom"],
  env: {
    // Server code alone imports this; resolve independently of launch cwd.
    DIORAMA_DEFAULT_LIBRARY: path.resolve(root, "../.diorama"),
  },
};
export default config;
