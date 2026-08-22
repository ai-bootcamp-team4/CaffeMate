export {}

declare global {
  interface Window {
    __CAFFEMATE_CONFIG__?: {
      CONTROL_API_BASE_URL?: string
      FIREBASE_API_KEY?: string
      FIREBASE_AUTH_DOMAIN?: string
      FIREBASE_PROJECT_ID?: string
      FIREBASE_APP_ID?: string
    }
  }
}
