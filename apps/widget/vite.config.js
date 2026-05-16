import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'
export default defineConfig({
  plugins: [preact()],
  build: {
    lib: {
      entry: 'src/index.jsx',
      name: 'VeridianWidget',
      fileName: 'widget',
      formats: ['iife']
    },
    minify: 'terser',
    terserOptions: { compress: { drop_console: true, passes: 2 }, mangle: true },
    rollupOptions: { output: { manualChunks: undefined, inlineDynamicImports: true } }
  }
})
