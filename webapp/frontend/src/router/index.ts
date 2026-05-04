import { createRouter, createWebHashHistory } from 'vue-router'
import BrowseView from '../views/BrowseView.vue'
import SearchView from '../views/SearchView.vue'
import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'
import SetPasswordView from '../views/SetPasswordView.vue'

import ItemView from '../views/ItemView.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      redirect: '/browse'
    },
    {
      path: '/item/:path(.*)*',
      name: 'item',
      component: ItemView
    },
    {
      path: '/browse/:path(.*)*',
      name: 'browse',
      component: BrowseView
    },
    {
      path: '/search',
      name: 'search',
      component: SearchView
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView
    },
    {
      path: '/register',
      name: 'register',
      component: RegisterView
    },
    {
      path: '/set-password',
      name: 'set-password',
      component: SetPasswordView
    }
  ]
})

export default router
