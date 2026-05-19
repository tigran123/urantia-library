import { createRouter, createWebHashHistory } from 'vue-router'
import BrowseView from '../views/BrowseView.vue'
import SearchView from '../views/SearchView.vue'
import BookshelfView from '../views/BookshelfView.vue'
import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'
import SetPasswordView from '../views/SetPasswordView.vue'

import ItemView from '../views/ItemView.vue'
import AdminUsersView from '../views/AdminUsersView.vue'
import AdminBooksView from '../views/AdminBooksView.vue'
import AdminIntegrityView from '../views/AdminIntegrityView.vue'
import AdminUploadView from '../views/AdminUploadView.vue'

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
      path: '/bookshelf',
      name: 'bookshelf',
      component: BookshelfView
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
    },
    {
      path: '/admin',
      redirect: '/admin/users'
    },
    {
      path: '/admin/users',
      name: 'admin-users',
      component: AdminUsersView
    },
    {
      path: '/admin/books',
      name: 'admin-books',
      component: AdminBooksView
    },
    {
      path: '/admin/integrity',
      name: 'admin-integrity',
      component: AdminIntegrityView
    },
    {
      path: '/admin/upload',
      name: 'admin-upload',
      component: AdminUploadView
    }
  ]
})

export default router
