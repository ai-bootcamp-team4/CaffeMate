import { getApp, getApps, initializeApp } from 'firebase/app'
import {
  GoogleAuthProvider,
  browserLocalPersistence,
  getAuth,
  onAuthStateChanged,
  setPersistence,
  signInWithPopup,
  signOut as firebaseSignOut,
  type User,
} from 'firebase/auth'

export interface AuthSession {
  uid: string
  displayName: string | null
  getIdToken(forceRefresh?: boolean): Promise<string>
  signOut(): Promise<void>
}

export interface AuthGateway {
  restoreSession(): Promise<AuthSession | null>
  signIn(): Promise<AuthSession>
}

type RuntimeConfigKey = 'FIREBASE_API_KEY' | 'FIREBASE_AUTH_DOMAIN' | 'FIREBASE_PROJECT_ID' | 'FIREBASE_APP_ID'

function requiredEnvironment(name: RuntimeConfigKey): string {
  const runtime = window.__CAFFEMATE_CONFIG__?.[name]
  const build = import.meta.env[`VITE_${name}`]
  const value = runtime || build
  if (!value) throw new Error(`FIREBASE_WEB_CONFIG_MISSING:${name}`)
  return value
}

function toSession(user: User): AuthSession {
  return {
    uid: user.uid,
    displayName: user.displayName,
    getIdToken: (forceRefresh = false) => user.getIdToken(forceRefresh),
    signOut: () => firebaseSignOut(getAuth()),
  }
}

export function createFirebaseAuthGateway(): AuthGateway {
  const app = getApps().length > 0
    ? getApp()
    : initializeApp({
        apiKey: requiredEnvironment('FIREBASE_API_KEY'),
        authDomain: requiredEnvironment('FIREBASE_AUTH_DOMAIN'),
        projectId: requiredEnvironment('FIREBASE_PROJECT_ID'),
        appId: requiredEnvironment('FIREBASE_APP_ID'),
      })
  const auth = getAuth(app)

  return {
    restoreSession: () => new Promise((resolve, reject) => {
      const unsubscribe = onAuthStateChanged(
        auth,
        (user) => {
          unsubscribe()
          resolve(user ? toSession(user) : null)
        },
        reject,
      )
    }),
    signIn: async () => {
      await setPersistence(auth, browserLocalPersistence)
      const credential = await signInWithPopup(auth, new GoogleAuthProvider())
      return toSession(credential.user)
    },
  }
}
