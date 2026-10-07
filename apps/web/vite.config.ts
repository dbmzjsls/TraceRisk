// Adapted from vue-naive-admin fixed source. Copyright © 2023 Ronnie Zhang(大脸怪) | https://isme.top
import Vue from '@vitejs/plugin-vue'
import Unocss from 'unocss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [Vue(), Unocss()],
  server: {
    host: '127.0.0.1', port: 3200, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, rewrite: path => path.replace(/^\/api/, '') } },
  },
})
