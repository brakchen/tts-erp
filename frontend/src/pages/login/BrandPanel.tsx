import AppBrand from '../../app/AppBrand';

/** Login-only brand composition. The illustration is decorative, not business data. */
export default function BrandPanel() {
  return <section className="login-brand" aria-label="TTS ERP 店铺运营工作台">
    <AppBrand className="login-wordmark" />
    <div className="login-brand-story">
      <p className="login-eyebrow">YOUR BUSINESS, IN FOCUS</p>
      <h2 className="login-brand-title">把经营，<br />看得更清楚<span>。</span></h2>
      <p className="login-brand-description">从每一笔订单，到每一次增长。<br />让数据成为日常经营的底气。</p>
      <div className="login-illustration" aria-hidden="true">
        <svg viewBox="0 0 480 170" fill="none">
          <path d="M0 30h480M0 85h480M0 140h480M45 0v170M145 0v170M245 0v170M345 0v170M445 0v170" stroke="currentColor" strokeOpacity=".13" />
          <path d="M5 146 64 126 121 134 179 91 237 107 294 62 353 76 411 33 470 15" stroke="var(--navigation-active)" strokeWidth="2.5" strokeLinejoin="round" />
          <path d="m5 159 59-10 57 4 58-30 58 6 57-22 59 5 58-33 59-8" stroke="currentColor" strokeOpacity=".36" strokeWidth="1.5" strokeDasharray="5 6" />
          <circle cx="294" cy="62" r="6" fill="var(--navigation-active)" stroke="var(--navigation-background)" strokeWidth="3" />
          <circle cx="470" cy="15" r="6" fill="var(--navigation-active)" stroke="var(--navigation-background)" strokeWidth="3" />
        </svg>
      </div>
    </div>
    <div className="login-brand-bottom">
      <ul aria-label="工作台功能" className="login-features"><li>订单数据</li><li>广告表现</li><li>商品利润</li></ul>
      <p>TIKTOK SHOP · 运营分析工作台</p>
    </div>
  </section>;
}
