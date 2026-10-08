interface ImportMetaEnv {
  readonly WXT_API_BASE?: string;
  readonly WXT_PAIR_URL?: string;
  readonly WXT_DIAGNOSTICS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
