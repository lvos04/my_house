/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set at image build (APP_VERSION build arg); vite.config.ts falls back to the repo VERSION file. */
  readonly VITE_APP_VERSION?: string;
}
