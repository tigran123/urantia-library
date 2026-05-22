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
import AdminCommentsView from '../views/AdminCommentsView.vue'
import FeedbackComposeView from '../views/FeedbackComposeView.vue'
import MyFeedbackView from '../views/MyFeedbackView.vue'
import FeedbackThreadView from '../views/FeedbackThreadView.vue'
import AdminFeedbackView from '../views/AdminFeedbackView.vue'
import AdminFeedbackSettingsView from '../views/AdminFeedbackSettingsView.vue'

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
    },
    {
      path: '/admin/comments',
      name: 'admin-comments',
      component: AdminCommentsView
    },
    {
      path: '/feedback',
      name: 'feedback-compose',
      component: FeedbackComposeView
    },
    {
      path: '/feedback/mine',
      name: 'feedback-mine',
      component: MyFeedbackView
    },
    {
      path: '/feedback/:publicId',
      name: 'feedback-thread',
      component: FeedbackThreadView,
      props: true
    },
    {
      path: '/admin/feedback',
      name: 'admin-feedback',
      component: AdminFeedbackView
    },
    {
      path: '/admin/feedback/settings',
      name: 'admin-feedback-settings',
      component: AdminFeedbackSettingsView
    }
  ]
})

export default router
