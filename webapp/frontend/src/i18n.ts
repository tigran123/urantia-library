import { createI18n } from 'vue-i18n'

const messages = {
  en: {
    app: {
      title: 'Urantia Library',
      search_placeholder: 'Search library...',
      logout: 'Logout',
      footer: '© 2026 Urantia Library',
      download: 'Download',
      preview: 'Preview',
      preview_not_available: 'Preview not available for this file format.',
      please_download: 'Please download the file to view it.',
      format: 'Format',
      size: 'Size',
      modified: 'Modified',
      unknown: 'Unknown'
    },
    auth: {
      signInTitle: 'Sign In',
      emailLabel: 'Email',
      emailRequiredLabel: 'Email *',
      passwordLabel: 'Password',
      signInLoading: 'Signing in...',
      signInBtn: 'Sign In',
      noAccount: "Don't have an account?",
      requestAccessLink: 'Request access',
      loginError: 'An error occurred during login.',
      registerTitle: 'Request Access',
      registerSuccess: 'Registration request queued! An admin will review it shortly. You will receive an email upon approval or denial.',
      registerError: 'An error occurred during registration.',
      optionalNote: 'Note: The following fields are optional, but omitting this context may cause delays in approving your registration.',
      sourceLabel: 'Where did you hear about the library?',
      sourcePlaceholder: 'e.g., Friend, Google, Forum...',
      purposeLabel: 'Purpose for registering',
      purposePlaceholder: 'Why would you like access to this library?',
      submitLoading: 'Submitting...',
      submitBtn: 'Submit Request',
      alreadyHaveAccount: 'Already have an account?'
    }
  },
  ru: {
    app: {
      title: 'Библиотека Урантии',
      search_placeholder: 'Поиск по библиотеке...',
      logout: 'Выйти',
      footer: '© 2026 Библиотека Урантии',
      download: 'Скачать',
      preview: 'Предпросмотр',
      preview_not_available: 'Предпросмотр недоступен для этого формата файла.',
      please_download: 'Пожалуйста, скачайте файл для просмотра.',
      format: 'Формат',
      size: 'Размер',
      modified: 'Изменён',
      unknown: 'Неизвестно'
    },
    auth: {
      signInTitle: 'Вход',
      emailLabel: 'Почта',
      emailRequiredLabel: 'Почта *',
      passwordLabel: 'Пароль',
      signInLoading: 'Вход...',
      signInBtn: 'Войти',
      noAccount: 'Нет аккаунта?',
      requestAccessLink: 'Запросить доступ',
      loginError: 'Произошла ошибка при входе.',
      registerTitle: 'Запрос доступа',
      registerSuccess: 'Запрос на регистрацию добавлен в очередь! Администратор рассмотрит его в ближайшее время. Вы получите письмо с уведомлением об одобрении или отказе.',
      registerError: 'Произошла ошибка при регистрации.',
      optionalNote: 'Примечание: Следующие поля не обязательны, но отсутствие этой информации может привести к задержкам при одобрении вашей регистрации.',
      sourceLabel: 'Откуда вы узнали о библиотеке?',
      sourcePlaceholder: 'например: от друга, из Google, на форуме...',
      purposeLabel: 'Цель регистрации',
      purposePlaceholder: 'Почему вы хотите получить доступ к этой библиотеке?',
      submitLoading: 'Отправка...',
      submitBtn: 'Отправить запрос',
      alreadyHaveAccount: 'Уже есть аккаунт?'
    }
  }
}

const savedLocale = localStorage.getItem('locale') || 'en'

export const i18n = createI18n({
  legacy: false, // Set to false to use Composition API
  locale: savedLocale,
  fallbackLocale: 'en',
  messages,
})

