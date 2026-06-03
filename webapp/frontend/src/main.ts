import { createApp } from 'vue'
import './style.css'
import './composables/useTextSize' // applies the saved root font-size before mount
import App from './App.vue'
import router from './router'
import { i18n } from './i18n'

createApp(App).use(router).use(i18n).mount('#app')