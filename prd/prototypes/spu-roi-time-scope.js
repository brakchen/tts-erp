/* 仅联动原型的日期与预置示例，不请求后端，不计算财务指标。 */
(function () {
  function bind() {
    const root = document.getElementById('spu-time-scope');
    if (!root) return;
    const start = root.querySelector('#filter-w-start');
    const end = root.querySelector('#filter-w-end');
    const lookback = root.querySelector('#filter-projection-lookback-days');
    const sample = {
      '30': { window: '2026-10-20 ~ 2026-11-18', orders: '1,204', losses: '38', rate: '3.16%' },
      '90': { window: '2026-08-21 ~ 2026-11-18', orders: '3,672', losses: '110', rate: '3.00%' },
    };
    function set(id, value) { root.querySelector('#' + id).textContent = value; }
    function applyRange() {
      const error = root.querySelector('#scope-date-error');
      if (start.value && end.value && start.value > end.value) {
        error.textContent = '起始日不能晚于截止日。';
        return;
      }
      error.textContent = '';
      const label = !start.value && !end.value ? '全部经营日期' : (start.value || '不限') + ' ~ ' + (end.value || '不限');
      root.querySelectorAll('[data-report-range]').forEach(function (el) { el.textContent = label; });
      root.querySelectorAll('[data-range]').forEach(function (button) {
        button.setAttribute('aria-pressed', button.dataset.range === start.value + '/' + end.value ? 'true' : 'false');
      });
    }
    function applySample() {
      const value = sample[lookback.value];
      set('projection-sample-window', value.window);
      set('sum-projection-completed-basis-orders', value.orders);
      set('sum-projection-completed-loss-orders', value.losses);
      set('sum-projection-full-loss-rate', value.rate);
      set('scope-sample-link', lookback.value + ' 天样本');
    }
    start.addEventListener('change', applyRange);
    end.addEventListener('change', applyRange);
    root.querySelectorAll('[data-range]').forEach(function (button) {
      button.addEventListener('click', function () {
        const values = button.dataset.range.split('/');
        start.value = values[0];
        end.value = values[1];
        applyRange();
      });
    });
    lookback.addEventListener('change', applySample);
    root.querySelector('#scope-shop').addEventListener('change', function (event) {
      const name = event.target.selectedOptions[0].textContent;
      root.querySelectorAll('[data-shop-name]').forEach(function (el) { el.textContent = name; });
    });
    applyRange();
    applySample();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
