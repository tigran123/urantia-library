import { createRouter, createWebHashHistory } from 'vue-router'
import { ensureAuthLoaded } from '../auth'
import BrowseView from '../views/BrowseView.vue'
import SearchView from '../views/SearchView.vue'
import PlaylistsView from '../views/PlaylistsView.vue'
import PlaylistDetailView from '../views/PlaylistDetailView.vue'
import PublicPlaylistView from '../views/PublicPlaylistView.vue'
import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'
import SetPasswordView from '../views/SetPasswordView.vue'
import ForgotPasswordView from '../views/ForgotPasswordView.vue'
import ResetPasswordView from '../views/ResetPasswordView.vue'

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
import AdminAuditView from '../views/AdminAuditView.vue'
import PrivacyView from '../views/PrivacyView.vue'
import TermsView from '../views/TermsView.vue'
import WelcomeView from '../views/WelcomeView.vue'
import MyActivityView from '../views/MyActivityView.vue'
import AdminUsageView from '../views/AdminUsageView.vue'

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
      path: '/playlists',
      name: 'playlists',
      component: PlaylistsView
    },
    {
      path: '/playlists/:id',
      name: 'playlist-detail',
      component: PlaylistDetailView,
      props: true
    },
    {
      // Public share link — works for anonymous viewers; renders recipient
      // chrome (wordmark + "Sign in to save", no app nav) via route meta.
      path: '/p/:token',
      name: 'playlist-share',
      component: PublicPlaylistView,
      props: true,
      meta: { publicChrome: true }
    },
    {
      // Old My Bookshelf link/bookmarks now land on the playlists index, where
      // the default Bookshelf lives.
      path: '/bookshelf',
      redirect: '/playlists'
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
      path: '/forgot-password',
      name: 'forgot-password',
      component: ForgotPasswordView
    },
    {
      path: '/reset-password',
      name: 'reset-password',
      component: ResetPasswordView
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
    },
    {
      path: '/admin/audit',
      name: 'admin-audit',
      component: AdminAuditView
    },
    {
      path: '/welcome',
      name: 'welcome',
      component: WelcomeView
    },
    {
      path: '/privacy',
      name: 'privacy',
      component: PrivacyView
    },
    {
      path: '/terms',
      name: 'terms',
      component: TermsView
    },
    {
      path: '/account/activity',
      name: 'my-activity',
      component: MyActivityView
    },
    {
      path: '/admin/usage',
      name: 'admin-usage',
      component: AdminUsageView
    }
  ]
})

// Single admin guard for the whole /admin/* surface, so individual admin views
// can't forget it (and the three that historically had no client-side check are
// now covered too). It awaits ensureAuthLoaded() because currentUser is fetched
// async — a guard that merely read the ref would race a cold reload and bounce
// admins. The backend still enforces admin on every /api/admin/* call; this is UX.
router.beforeEach(async (to) => {
  if (!(to.path === '/admin' || to.path.startsWith('/admin/'))) return true
  const user = await ensureAuthLoaded()
  if (!user) {
    return { name: 'login', query: to.fullPath !== '/' ? { next: to.fullPath } : undefined }
  }
  if (!user.is_admin) return { name: 'browse' }
  return true
})

export default router
