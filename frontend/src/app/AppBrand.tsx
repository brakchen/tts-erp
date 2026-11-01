export default function AppBrand({ className = '' }: { className?: string }) {
  return <div className={`erp-brand ${className}`} aria-label="TTS ERP">
    <svg aria-hidden="true" width="30" height="30" viewBox="0 0 34 34" fill="none"><path d="M5 8h24M17 8v21M5 17h7M24 17h5" stroke="currentColor" strokeWidth="3.5" /></svg>
    <span>tts<span className="erp-brand-suffix"> / erp</span></span>
  </div>;
}
