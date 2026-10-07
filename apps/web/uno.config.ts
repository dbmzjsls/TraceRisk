// Adapted from vue-naive-admin. Copyright © 2023 Ronnie Zhang(大脸怪) | https://isme.top
import { defineConfig, presetWind3 } from 'unocss'

export default defineConfig({
  presets: [presetWind3()],
  shortcuts: [['wh-full', 'w-full h-full'], ['f-c-c', 'flex justify-center items-center'], ['flex-col', 'flex flex-col'], ['auto-bg', 'bg-white dark:bg-dark']],
  rules: [['card-shadow', { 'box-shadow': '0 1px 2px -2px #00000029, 0 3px 6px #0000001f, 0 5px 12px 4px #00000017' }]],
  theme: { colors: { dark: '#18181c', light_border: '#efeff5', dark_border: '#2d2d30' } },
})
