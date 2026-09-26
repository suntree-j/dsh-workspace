/* ============================================================
 * 批流一体智能数据分析平台 —— 静态前端看板（无构建步骤）
 * ============================================================
 *
 * 设计约束与取舍（写在这里，便于论文与答辩时对照说明）：
 *
 * 1. 不使用 npm / vite / webpack，也不使用 ES module（无 import/export）。
 *    Vue 3 与 ECharts 由 scripts/install-web.sh 下载到 ./vendor/，
 *    这里通过 window.Vue / window.echarts 访问全局构建。
 *
 * 2. 后端返回的信封统一为 { data, source, generated_at }，
 *    因此 apiGet() 只负责"取信封里的 data 并翻译错误"，
 *    业务代码拿到的就是干净数据，不需要每处都判断 code / error。
 *
 * 3. 金额字段（DECIMAL）后端以字符串返回（如 "23616.00"），
 *    前端一律按字符串处理，需要跨行累加时先转成「整数分」（BigInt）再运算，
 *    绝不使用 parseFloat 累加，避免 0.1 + 0.2 类的精度问题。
 *
 * 4. 比率为 null 表示"分母为 0，无法计算"，必须显示 —；
 *    可加指标后端已补 0，直接显示 0。两者不能混为一谈。
 * ============================================================ */

(function () {
  'use strict';

  // ---------- 依赖就绪检查 ----------
  // Vue / ECharts 是 <script> 同步加载的，执行到此处必然已就绪或必然缺失；
  // 缺失时给出可操作的中文提示（而不是留下白屏让答辩现场尴尬）。
  var bootEl = document.getElementById('boot');
  var hasVue = typeof window.Vue !== 'undefined' && typeof window.Vue.createApp === 'function';
  var hasECharts = typeof window.echarts !== 'undefined';

  if (!hasVue || !hasECharts) {
    if (bootEl) {
      bootEl.classList.add('boot--error');
      // 先清空占位文案再写入提示，避免出现"正在加载…"与错误信息并存的错觉
      bootEl.textContent = '';
      bootEl.innerHTML =
        '<div class="boot__box">' +
        '<h2 class="boot__title">前端依赖未就绪，请先执行 bash scripts/install-web.sh</h2>' +
        '<p class="boot__line">缺少依赖：' + (!hasVue ? 'Vue 3（./vendor/vue.global.prod.js）' : '') +
        (!hasVue && !hasECharts ? '、' : '') + (!hasECharts ? 'ECharts（./vendor/echarts.min.js）' : '') + '</p>' +
        '<p class="boot__line">该脚本会把两个运行时库下载到 services/web/vendor/ 目录，本页面不引入任何外网 CDN。</p>' +
        '<p class="boot__line">若已执行脚本仍看到本提示，请检查浏览器开发者工具的 Network 面板中 ./vendor/ 下两个文件是否返回 200。</p>' +
        '</div>';
    }
    return;
  }

  var Vue = window.Vue;
  var echarts = window.echarts;
  var ref = Vue.ref;
  var computed = Vue.computed;
  var reactive = Vue.reactive;
  var watch = Vue.watch;
  var onMounted = Vue.onMounted;
  var onBeforeUnmount = Vue.onBeforeUnmount;
  var createApp = Vue.createApp;

  // ============================================================
  // ---------- 常量与元信息 ----------
  // ============================================================

  // 由 index.html 的 <meta name="api-base"> 提供；
  // 取不到时回退到相对路径 ./api，保证在任意子路径下都能工作（不拼接域名）。
  var API_BASE = (function () {
    var meta = document.querySelector('meta[name="api-base"]');
    var value = meta && meta.getAttribute('content');
    return (value && value.trim()) || './api';
  })();

  // 图表配色：深色大屏下保持低饱和、高区分度，不使用渐变与动效
  var C = {
    gmv: '#2f81f7',
    pay: '#3fb950',
    order: '#8b949e',
    pv: '#2f81f7',
    uv: '#d29922',
    view: '#8b949e',
    accent: '#58a6ff',
    warn: '#d29922',
    danger: '#f85149',
    grid: 'rgba(148,163,184,0.16)',
    axis: '#8b949e',
    series: ['#2f81f7', '#3fb950', '#d29922', '#a371f7', '#f85149', '#39c5cf', '#db6d28', '#8b949e']
  };

  var COMMON_TIP = {
    backgroundColor: 'rgba(15,23,42,0.94)',
    borderColor: 'rgba(148,163,184,0.3)',
    borderWidth: 1,
    padding: [10, 12],
    textStyle: { color: '#e6edf3', fontSize: 12 },
    extraCssText: 'box-shadow:0 8px 24px rgba(2,6,23,0.6);border-radius:8px;'
  };

  // 类目页「全部」选项：窗口上限取一个足够大的值，语义等价于"不限窗口"
  var ALL_WINDOWS = 1000000;
  var PAGE_SIZE = 20;

  // ============================================================
  // ---------- 工具函数 ----------
  // ============================================================

  function isMissing(v) {
    return v === null || v === undefined || v === '';
  }

  function numberOrNull(v) {
    if (isMissing(v)) return null;
    var n = typeof v === 'number' ? v : Number(v);
    return isFinite(n) ? n : null;
  }

  // 千分位 + 固定 2 位小数。金额是字符串，这里按字符串补零，
  // 不经过 Number，规避大额金额的浮点截断。
  function formatMoney(v) {
    if (isMissing(v)) return '—';
    var s = String(v).trim();
    var m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s);
    if (!m) {
      var n = numberOrNull(s);
      if (n === null) return '—';
      s = n.toFixed(2);
      m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s);
      if (!m) return '—';
    }
    var sign = m[1];
    var intPart = m[2];
    var decPart = (m[3] || '') + '00';
    return sign + intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + decPart.slice(0, 2);
  }

  // 金额字符串 → 整数分（BigInt）。跨行累加只允许走这条路径，
  // 任何 parseFloat 累加都会在 6 位数金额上产生分级误差。
  function moneyToCents(v) {
    if (isMissing(v)) return null;
    var s = String(v).trim();
    var m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s);
    if (!m) return null;
    var dec = (m[3] || '') + '00';
    var cents = BigInt(m[2]) * 100n + BigInt(dec.slice(0, 2));
    return m[1] === '-' ? -cents : cents;
  }

  function formatCents(cents) {
    if (cents === null || cents === undefined) return '—';
    var neg = cents < 0n;
    var abs = neg ? -cents : cents;
    var intPart = (abs / 100n).toString();
    var dec = (abs % 100n).toString();
    if (dec.length < 2) dec = '0' + dec;
    return (neg ? '-' : '') + intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + dec;
  }

  // 金额数组求和 → 整数分；全部为空时返回 null，交给调用方决定显示 —
  function sumCents(values) {
    var total = 0n;
    var seen = false;
    for (var i = 0; i < values.length; i++) {
      var c = moneyToCents(values[i]);
      if (c === null) continue;
      total += c;
      seen = true;
    }
    return seen ? total : null;
  }

  function formatInt(v) {
    if (isMissing(v)) return '—';
    var n = numberOrNull(v);
    if (n === null) return '—';
    return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  // 比率：后端返回 DECIMAL(10,4) 字符串（0.9780）或 null（分母为 0）
  function formatRate(v) {
    var n = numberOrNull(v);
    if (n === null) return '—';
    return (n * 100).toFixed(2) + '%';
  }

  // 整数比率：两侧都是整数计数，用整数运算避免 0.1% 级抖动
  function ratioOf(part, whole) {
    if (isMissing(part) || isMissing(whole)) return null;
    if (!whole) return null;
    return part / whole;
  }

  function formatTime(v) {
    if (isMissing(v)) return '—';
    return String(v);
  }

  // 图表 X 轴只需要 HH:MM，窗口字段固定为 "YYYY-MM-DD HH:MM:SS"
  function shortTime(v) {
    if (isMissing(v)) return '';
    var s = String(v);
    return s.length >= 16 ? s.slice(11, 16) : s;
  }

  // generated_at 是带时区的 ISO 串，统一显示为服务器本地时间样式
  function formatDateTime(v) {
    if (isMissing(v)) return '—';
    var d = new Date(v);
    if (isNaN(d.getTime())) return String(v);
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' +
      p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function clamp(v, min, max) {
    var n = numberOrNull(v);
    if (n === null) return min;
    return Math.min(Math.max(Math.round(n), min), max);
  }

  // ECharts 不接受 Vue 的响应式代理对象：深拷贝成纯对象再渲染，
  // 同时也能避免图表内部持有代理引用导致的内存滞留。
  function toPlain(value) {
    return JSON.parse(JSON.stringify(value === undefined ? null : value));
  }

  function joinText(list, sep) {
    if (!list || !list.length) return '—';
    return list.join(sep || '、');
  }

  // ============================================================
  // ---------- API 客户端 ----------
  // ============================================================

  function ApiError(message, code, detail, status) {
    this.name = 'ApiError';
    this.message = message;
    this.code = code || '';
    this.detail = detail || '';
    this.status = status || 0;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function buildUrl(path, params) {
    var base = API_BASE.replace(/\/+$/, '');
    var url = base + path;
    if (!params) return url;
    var parts = [];
    Object.keys(params).forEach(function (key) {
      var value = params[key];
      // 仅跳过 undefined / null：空字符串是「显式不筛选」也可能有意义，
      // 但订单筛选中空串代表"不限"，后端也能接受，这里统一保留语义给调用方决定。
      if (value === undefined || value === null) return;
      parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(String(value)));
    });
    return parts.length ? url + '?' + parts.join('&') : url;
  }

  /**
   * 统一 GET：拼 query → 解析信封 → 出错时抛出带中文 message 的 ApiError。
   * 返回信封中的 data 字段（业务代码不再关心信封结构）。
   */
  function apiGet(path, params) {
    var url = buildUrl(path, params);
    var init = { method: 'GET', headers: { Accept: 'application/json' }, cache: 'no-store' };
    return fetch(url, init).then(function (resp) {
      return resp.text().then(function (text) {
        var body = null;
        try {
          body = text ? JSON.parse(text) : null;
        } catch (e) {
          body = null;
        }
        if (!resp.ok) {
          var err = body && body.error ? body.error : null;
          throw new ApiError(
            (err && err.message) || ('接口请求失败（HTTP ' + resp.status + '）'),
            (err && err.code) || 'HTTP_' + resp.status,
            (err && err.detail) || '',
            resp.status
          );
        }
        if (!body || typeof body !== 'object' || !('data' in body)) {
          throw new ApiError('接口返回格式不符合约定（缺少 data 字段）', 'BAD_ENVELOPE', '', resp.status);
        }
        return body.data;
      });
    }, function (cause) {
      // 网络层失败（后端未启动 / Nginx 未转发）与业务错误分开提示，便于定位
      throw new ApiError('无法连接到后端数据接口，请确认 API 服务与 Nginx 反向代理已启动', 'NETWORK_ERROR',
        String((cause && cause.message) || cause), 0);
    });
  }

  // 各页面对应的后端端点集中在此，避免路径散落在业务代码里
  var api = {
    health: function () { return apiGet('/health'); },
    overview: function (windowLimit) { return apiGet('/overview', { window_limit: windowLimit }); },
    trade: function (limit) { return apiGet('/metrics/trade', { limit: limit }); },
    traffic: function (limit) { return apiGet('/metrics/traffic', { limit: limit }); },
    category: function (limit, windowLimit) {
      return apiGet('/metrics/category', { limit: limit, window_limit: windowLimit });
    },
    funnel: function (windowLimit) { return apiGet('/funnel', { window_limit: windowLimit }); },
    orders: function (params) { return apiGet('/orders', params); },
    orderDetail: function (orderId) { return apiGet('/orders/' + encodeURIComponent(orderId)); },
    metaMetrics: function () { return apiGet('/meta/metrics'); },
    metaTables: function () { return apiGet('/meta/tables'); }
  };

  // ============================================================
  // ---------- 指标口径缓存 ----------
  // ============================================================

  // KPI 卡片的 tooltip 直接引用后端指标字典的 definition 字段（要求 3.1），
  // 接口不可用时退回 sql/metadata/metrics.md 的原文，保证看板不出现空白提示。
  var DEFINITION_FALLBACK = {
    gmv: '窗口内订单创建事件（ORDER_CREATED）的成交金额合计：SUM(amount)。',
    order_cnt: '窗口内订单创建事件数：COUNT(*) WHERE event_type=\'ORDER_CREATED\'。',
    order_user_cnt: '窗口内产生订单的去重用户数：COUNT(DISTINCT user_id)。',
    avg_order_amount: '客单价 = gmv / order_cnt；分母为 0 时返回 NULL。',
    payment_cnt: '窗口内支付成功事件数：COUNT(*) WHERE event_type=\'PAYMENT_SUCCESS\'。',
    payment_amount: '窗口内支付成功金额合计。',
    payment_success_rate: '支付成功率 = payment_cnt / (payment_cnt + payment_fail_cnt)；分母为 0 时返回 NULL。',
    refund_cnt: '窗口内退款发起事件数：COUNT(*) WHERE event_type=\'REFUND_CREATED\'。',
    refund_amount: '窗口内退款金额合计：SUM(refund_amount)。',
    refund_rate: '退款率 = refund_amount / payment_amount（金额口径）；分母为 0 时返回 NULL。',
    uv: '窗口内去重用户数：COUNT(DISTINCT user_id)。',
    pv: '窗口内行为事件总数：COUNT(*)，含 VIEW/CLICK/CART/FAVORITE/BUY。',
    view_cnt: 'VIEW 行为事件数；漏斗第一层。',
    click_cnt: 'CLICK 行为事件数。',
    cart_cnt: 'CART（加购）行为事件数。',
    buy_cnt: 'BUY（购买）行为事件数；漏斗最后一层。',
    favorite_cnt: 'FAVORITE（收藏）行为事件数。',
    click_rate: '点击率 = click_cnt / view_cnt；分母为 0 时返回 NULL。',
    cart_rate: '加购率 = cart_cnt / click_cnt；分母为 0 时返回 NULL。',
    buy_rate: '购买转化率 = buy_cnt / cart_cnt；分母为 0 时返回 NULL。',
    total_quantity: '窗口内该类目商品件数合计：SUM(quantity)。'
  };

  var definitionCache = reactive({});
  var definitionLoaded = false;

  function definitionOf(field) {
    return definitionCache[field] || DEFINITION_FALLBACK[field] || '';
  }

  // 只预加载一次；失败不提示、不阻塞——口径提示属于增强信息，
  // 各页面自身的数据加载失败才需要显式报错。
  //
  // 注意接口返回结构：/meta/metrics 的 data 是**对象**
  //   { metrics: [...], conventions: [...], version, updated_at, source_document }
  // 而不是裸数组 —— 它同时携带口径文档的版本与通用约定，
  // 前端必须取 data.metrics（曾经直接当数组用，导致字典页显示"没有匹配的指标定义"）。
  function metricList(payload) {
    if (Array.isArray(payload)) return payload;
    return (payload && payload.metrics) || [];
  }

  function preloadDefinitions() {
    if (definitionLoaded) return;
    definitionLoaded = true;
    api.metaMetrics().then(function (payload) {
      metricList(payload).forEach(function (item) {
        if (item && item.field && item.definition) definitionCache[item.field] = item.definition;
      });
    }).catch(function () {
      definitionLoaded = false;
    });
  }

  // ============================================================
  // ---------- 图表生命周期 ----------
  // ============================================================

  // 统一的图表渲染封装：所有图表都必须经过这里。
  //   - 已存在实例则复用（不重复 init，避免泄漏）；
  //   - 先 clear 再 setOption(notMerge)：数据条数变化时不残留上一次的 series；
  //   - 统一关闭动画：数据大屏以"读数"为目的，动画只会干扰刷新时的对比。
  function renderChart(el, option) {
    if (!el || !option || !window.echarts) return null;
    var instance = echarts.getInstanceByDom(el) || echarts.init(el, null, { renderer: 'canvas' });
    instance.clear();
    instance.setOption(Object.assign({ animation: false, backgroundColor: 'transparent' }, option), true);
    return instance;
  }

  // 画布尺寸同步：容器尺寸变化时 resize，避免 ECharts 沿用首次 init 的旧尺寸
  function resizeChart(instance, el) {
    if (!instance || !el) return;
    if (instance.getWidth() !== el.clientWidth || instance.getHeight() !== el.clientHeight) {
      instance.resize();
    }
  }

  /**
   * 绑定一个图表容器：
   *   - 容器出现后自动 init，容器消失（切页 / v-if 移除）后自动 dispose；
   *   - option 依赖的数据变化时自动重绘；
   *   - 容器尺寸变化时自动 resize（ResizeObserver）。
   *
   * 为什么不能"没尺寸就 return"（本项目实际踩过的坑）：
   *   图表的容器用 v-show 控制显隐，首屏数据未到达时 display:none，
   *   clientWidth/clientHeight 都是 0。若此时直接 return，观察器就没有机会被注册，
   *   等 v-show 变为可见时既不会 init 也不会 resize —— 图表永远空白。
   *   另外 onMounted 与 watch(flush:'post') 都可能早于"移除 display:none"的那次 patch，
   *   所以读取 DOM 尺寸必须推迟到 nextTick 之后。
   *
   * 因此这里采用三段式保险，全部有上限，渲染成功后立即停止：
   *   1) 只要容器元素存在就先挂 ResizeObserver（幂等，只建一次），
   *      可见性/尺寸变化时再走完整的"init + 渲染"路径；
   *   2) 快路径：requestAnimationFrame 最多 30 帧（约 0.5 秒），
   *      覆盖"可见性变了但尺寸数值没变、ResizeObserver 不触发回调"的边角情况；
   *   3) 慢路径：定时退避重试（最多 40 次，约 16 秒），覆盖后端慢、元素尚未出现，
   *      并兜住后台标签页里 rAF 被浏览器节流的情形。
   */
  function bindChart(getEl, buildOption) {
    var inst = null;
    var observer = null;
    var observed = null;
    var retryTimer = null;
    var rafId = null;
    var retriesLeft = 0;
    var framesLeft = 0;
    // settled：已经用有效 option 渲染过一次。init 成功但 option 仍为 null 时不算 settled，
    // 必须继续重试，否则数据到达后就没人再画了。
    var settled = false;

    function clearRetry() {
      if (retryTimer !== null) { clearTimeout(retryTimer); retryTimer = null; }
      if (rafId !== null && typeof cancelAnimationFrame === 'function') { cancelAnimationFrame(rafId); }
      rafId = null;
    }

    function teardown() {
      retriesLeft = 0;
      framesLeft = 0;
      settled = true;
      clearRetry();
      if (observer) { observer.disconnect(); observer = null; observed = null; }
      if (inst) { inst.dispose(); inst = null; }
    }

    // 快路径：下一绘制帧再试一次（最多 30 帧）
    function scheduleFrame() {
      if (settled || framesLeft <= 0 || rafId !== null) return;
      if (typeof requestAnimationFrame !== 'function') { framesLeft = 0; return; }
      rafId = requestAnimationFrame(function () {
        rafId = null;
        framesLeft -= 1;
        draw();
      });
    }

    // 慢路径：有上限的指数退避重试：120ms 起，×1.5 递增，最多 40 次（约 16 秒）
    function scheduleRetry() {
      if (settled || retriesLeft <= 0 || retryTimer !== null) return;
      var delay = Math.min(120 * Math.pow(1.5, 40 - retriesLeft), 3000);
      retryTimer = setTimeout(function () {
        retryTimer = null;
        retriesLeft -= 1;
        draw();
      }, delay);
    }

    function isSized(el) {
      return !!(el && el.clientWidth > 0 && el.clientHeight > 0);
    }

    // 真正做一次"取元素 → 需要时 init → 按当前数据渲染"
    function renderInto(el) {
      if (!isSized(el)) return false; // 仍不可见（display:none）→ 交给观察器与重试
      var option;
      try {
        option = buildOption();
      } catch (e) {
        return true; // 数据本身有问题，交给各页面自己的错误处理，不做无意义重试
      }
      if (!option) return false;    // 数据还没到 → 继续重试
      resizeChart(inst, el);        // 尺寸变过则先 resize，再整幅重绘
      inst = renderChart(el, option) || inst;
      if (inst) inst.resize();
      return true;
    }

    function ensureObserver(el) {
      if (observed === el) return;
      if (observer) { observer.disconnect(); observer = null; }
      observed = null;
      if (typeof ResizeObserver === 'undefined' || !el) return;
      observer = new ResizeObserver(function () {
        // 用 rAF 把回调推迟一帧：避免与 Vue 的 DOM 补丁在同一帧里互相触发
        if (typeof requestAnimationFrame === 'function') {
          rafId = requestAnimationFrame(function () { rafId = null; draw(); });
        } else {
          draw();
        }
      });
      observer.observe(el);
      observed = el;
    }

    function draw() {
      var el = getEl();
      if (!el) {                       // 容器还没出现（v-if / 组件刚创建）
        // 注意：这里不能 teardown —— 组件仍在，只是 DOM 尚未就绪
        retriesLeft = Math.max(retriesLeft, 20);
        scheduleFrame();
        scheduleRetry();
        return;
      }
      ensureObserver(el);              // 先建立观察机制，再判断能否立刻渲染
      if (!renderInto(el)) {
        // 还没就绪：快路径（帧）与慢路径（定时）同时排队，先到者生效
        retriesLeft = Math.max(retriesLeft, 20);
        scheduleFrame();
        scheduleRetry();
      } else {
        settled = true;
        retriesLeft = 0;
        framesLeft = 0;
        clearRetry();
      }
    }

    // DOM 读取统一推迟到 nextTick：onMounted 与 watch(flush:'post') 都可能
    // 早于"移除 v-show 的 display:none"这次 patch，此时容器的 clientWidth 还是 0。
    // 每次重新调度都重置两路重试预算，保证切页/刷新后仍有完整的重试机会。
    function scheduleDraw() {
      retriesLeft = 40;
      framesLeft = 30;
      Vue.nextTick(function () {
        draw();
        scheduleFrame();               // draw 若判定未就绪，这里继续排下一帧
        scheduleRetry();
      });
    }

    watch(buildOption, scheduleDraw, { flush: 'post' });
    onMounted(scheduleDraw);
    onBeforeUnmount(teardown);
    return { draw: scheduleDraw, dispose: teardown };
  }

  // axis / series 的小工厂：六个页面共用同一套坐标轴样式，避免样式漂移
  function makeAxis(kind, opts) {
    var base = {
      type: kind,
      axisLine: { lineStyle: { color: C.grid } },
      axisTick: { show: false },
      axisLabel: { color: C.axis, fontSize: 11 },
      splitLine: { show: kind === 'value', lineStyle: { color: C.grid, type: 'dashed' } }
    };
    return Object.assign(base, opts || {});
  }

  function makeLine(name, data, color, yIndex) {
    return {
      name: name,
      type: 'line',
      yAxisIndex: yIndex || 0,
      data: data,
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: { width: 2, color: color },
      itemStyle: { color: color },
      emphasis: { focus: 'series' }
    };
  }

  // 图表使用的时间轴配置：X 轴只显示 HH:MM（要求：不展示完整日期）
  function timeAxis(labels) {
    return makeAxis('category', {
      data: labels,
      boundaryGap: true,
      splitLine: { show: false },
      axisLabel: { color: C.axis, fontSize: 11, hideOverlap: true }
    });
  }

  function moneyAxis() {
    return makeAxis('value', {
      axisLabel: {
        color: C.axis,
        fontSize: 11,
        formatter: function (v) { return formatMoney(v); }
      }
    });
  }

  function intAxis() {
    return makeAxis('value', {
      axisLabel: {
        color: C.axis,
        fontSize: 11,
        formatter: function (v) { return formatInt(v); }
      }
    });
  }

  // ============================================================
  // ---------- 页面通用状态（加载 / 错误 / 更新时间） ----------
  // ============================================================

  // 各面板挂载时登记自己的 reload；根组件的「刷新」按钮会逐个调用，
  // 因此新增页面只需登记一次，不必再改顶部逻辑。
  var reloadHooks = [];
  var globalError = ref('');
  var refreshing = ref(0);
  var latestStamp = ref('');

  function usePanel() {
    var loading = ref(false);
    var error = ref('');
    var updatedAt = ref('');

    function run(task, fallback) {
      if (loading.value) return Promise.resolve(null);
      loading.value = true;
      error.value = '';
      globalError.value = '';
      refreshing.value += 1;
      return task().then(function (result) {
        updatedAt.value = formatDateTime(new Date().toISOString());
        if (updatedAt.value > latestStamp.value) latestStamp.value = updatedAt.value;
        return result;
      }).catch(function (cause) {
        var message = (cause && cause.message) || '数据加载失败';
        var detail = cause && cause.detail ? '（' + cause.detail + '）' : '';
        error.value = message + detail;
        globalError.value = error.value;
        return typeof fallback === 'function' ? fallback() : (fallback === undefined ? null : fallback);
      }).then(function (result) {
        loading.value = false;
        refreshing.value -= 1;
        return result;
      });
    }

    function reload() {
      if (loading.value) return Promise.resolve(null);
      var fn = currentReload;
      return fn ? fn() : Promise.resolve(null);
    }

    var currentReload = null;
    function setReload(fn) { currentReload = fn; }

    onMounted(function () {
      if (currentReload) reloadHooks.push(reload);
    });
    onBeforeUnmount(function () {
      var i = reloadHooks.indexOf(reload);
      if (i >= 0) reloadHooks.splice(i, 1);
    });

    return {
      loading: loading,
      error: error,
      updatedAt: updatedAt,
      run: run,
      reload: reload,
      setReload: setReload,
      // 某些面板需要在不显示错误条的情况下自行处理失败（如详情弹窗）
      fail: function (cause) {
        return (cause && cause.message) || '数据加载失败';
      }
    };
  }

  // ============================================================
  // ---------- 路由（URL hash） ----------
  // ============================================================

  var ROUTES = [
    { key: 'overview', title: '总览', subtitle: '核心指标、交易与流量趋势、类目 Top5' },
    { key: 'trade', title: '交易分析', subtitle: 'GMV、客单价、支付成功率与退款率' },
    { key: 'traffic', title: '流量分析', subtitle: 'UV / PV、行为漏斗与转化率' },
    { key: 'category', title: '类目销售', subtitle: '类目 GMV 排行、占比与明细' },
    { key: 'orders', title: '订单明细', subtitle: '按类目与日期筛选，查看订单支付与退款记录' },
    { key: 'metrics', title: '指标口径', subtitle: '指标字典与表结构（Agent 回答问题的口径来源）' }
  ];

  var currentPage = ref('overview');

  function readHash() {
    var raw = String(window.location.hash || '').replace(/^#\/?/, '').split('?')[0];
    var found = ROUTES.some(function (r) { return r.key === raw; });
    return found ? raw : 'overview';
  }

  function setupRoute() {
    var sync = function () { currentPage.value = readHash(); };
    window.addEventListener('hashchange', sync);
    onMounted(function () {
      sync();
      // 把空 hash 规范化，便于复制链接直接进入某一页
      if (!window.location.hash) window.location.replace('#/' + currentPage.value);
    });
    onBeforeUnmount(function () { window.removeEventListener('hashchange', sync); });
    watch(currentPage, function () {
      window.scrollTo(0, 0);
    });
  }

  // ============================================================
  // ---------- 公共小组件 ----------
  // ============================================================

  var Icon = {
    name: 'Icon',
    template: '#tpl-icon',
    props: { name: { type: String, default: '' } }
  };

  var Skeleton = {
    name: 'Skeleton',
    template: '#tpl-skeleton',
    props: { rows: { type: Number, default: 4 } },
    methods: {
      // 让骨架条的宽度呈不规则分布，视觉上更接近真实内容
      barWidth: function (n) {
        var widths = ['92%', '76%', '84%', '68%', '88%', '72%'];
        return widths[n % widths.length];
      }
    }
  };

  var EmptyState = {
    name: 'EmptyState',
    template: '#tpl-empty',
    props: {
      text: { type: String, default: '' },
      hint: { type: String, default: '' }
    }
  };

  var KpiCard = {
    name: 'KpiCard',
    template: '#tpl-kpi',
    props: {
      label: { type: String, required: true },
      value: { default: null },
      unit: { type: String, default: '' },
      sub: { type: String, default: '' },
      tip: { type: String, default: '' }
    },
    computed: {
      // 面板传入的要么是已格式化字符串，要么是原始值；null 一律显示 —
      displayValue: function () {
        if (isMissing(this.value)) return '—';
        return String(this.value);
      },
      valueClass: function () {
        return this.displayValue === '—' ? 'is-missing' : '';
      }
    }
  };

  // 自绘下拉：原生 <select> 无法统一深色主题下的展开面板样式
  var SelectBox = {
    name: 'SelectBox',
    template: '#tpl-select',
    props: {
      modelValue: { default: '' },
      options: { type: Array, default: function () { return []; } }
    },
    emits: ['update:modelValue'],
    setup: function (props, ctx) {
      var open = ref(false);
      var root = ref(null);

      var selectedLabel = computed(function () {
        for (var i = 0; i < props.options.length; i++) {
          if (props.options[i].value === props.modelValue) return props.options[i].label;
        }
        return '请选择';
      });

      function pick(opt) {
        ctx.emit('update:modelValue', opt.value);
        open.value = false;
      }

      function onDocClick(event) {
        if (root.value && !root.value.contains(event.target)) open.value = false;
      }

      onMounted(function () { document.addEventListener('click', onDocClick); });
      onBeforeUnmount(function () { document.removeEventListener('click', onDocClick); });

      return { open: open, root: root, selectedLabel: selectedLabel, pick: pick };
    }
  };

  // ============================================================
  // ---------- 页面 1：总览 ----------
  // ============================================================

  var OverviewPanel = {
    name: 'OverviewPanel',
    template: '#tpl-overview',
    setup: function () {
      var panel = usePanel();
      var overview = ref(null);
      var trade = ref([]);
      var traffic = ref([]);

      var tradeEl = ref(null);
      var trafficEl = ref(null);
      var categoryEl = ref(null);

      var categoryTop = computed(function () {
        var data = overview.value;
        return (data && data.category_top) || [];
      });

      function load() {
        return panel.run(function () {
          return Promise.all([
            api.overview(60),
            api.trade(60),
            api.traffic(60)
          ]).then(function (res) {
            overview.value = res[0] || null;
            trade.value = res[1] || [];
            traffic.value = res[2] || [];
            return res[0] || null;
          });
        }, function () {
          // 单页失败时保留上一次成功的数据，避免整屏闪空
          return null;
        });
      }
      panel.setReload(load);
      onMounted(load);

      // —— KPI 卡片：GMV / 订单量 / 支付笔数 / 退款笔数 / UV / PV ——
      var kpiCards = computed(function () {
        var data = overview.value;
        if (!data) return [];
        var kpi = data.kpi || {};
        var windows = data.windows || {};
        var latest = trade.value.length ? trade.value[trade.value.length - 1] : null;
        var windowText = data.latest_window ? '窗口 ' + shortTime(data.latest_window) : '';
        var countText = '最近 ' + formatInt(windows.trade) + ' 个交易窗口';

        return [
          {
            label: 'GMV（下单金额）', value: formatMoney(kpi.gmv), unit: '元',
            sub: countText, tip: definitionOf('gmv')
          },
          {
            label: '订单量', value: formatInt(kpi.order_cnt), unit: '笔',
            sub: '下单用户 ' + formatInt(kpi.order_user_cnt) + ' 人', tip: definitionOf('order_cnt')
          },
          {
            label: '支付笔数', value: formatInt(kpi.payment_cnt), unit: '笔',
            sub: latest ? '当前窗口 ' + formatInt(latest.payment_cnt) + ' 笔' : windowText,
            tip: definitionOf('payment_cnt')
          },
          {
            label: '退款笔数', value: formatInt(kpi.refund_cnt), unit: '笔',
            sub: '退款金额 ' + formatMoney(kpi.refund_amount) + ' 元', tip: definitionOf('refund_cnt')
          },
          {
            label: 'UV（去重用户）', value: formatInt(kpi.uv), unit: '人',
            sub: '最近 ' + formatInt(windows.traffic) + ' 个流量窗口', tip: definitionOf('uv')
          },
          {
            label: 'PV（行为事件）', value: formatInt(kpi.pv), unit: '次',
            sub: windowText || '行为事件总数', tip: definitionOf('pv')
          }
        ];
      });

      // —— 交易趋势：GMV 折线 + 订单量柱 ——
      bindChart(function () { return tradeEl.value; }, function () {
        var rows = trade.value;
        if (!rows.length) return null;
        var labels = rows.map(function (r) { return shortTime(r.window_start); });
        return {
          color: [C.gmv, C.order],
          tooltip: Object.assign({ trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: '#1f2937' } } }, COMMON_TIP),
          legend: { data: ['GMV', '订单量'], right: 8, top: 0, textStyle: { color: C.axis, fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
          grid: { left: 8, right: 8, top: 40, bottom: 4, containLabel: true },
          xAxis: timeAxis(labels),
          yAxis: [moneyAxis(), intAxis()],
          series: [
            makeLine('GMV', rows.map(function (r) { return numberOrNull(r.gmv); }), C.gmv, 0),
            {
              name: '订单量', type: 'bar', yAxisIndex: 1,
              data: rows.map(function (r) { return numberOrNull(r.order_cnt); }),
              barMaxWidth: 14, itemStyle: { color: 'rgba(139,148,158,0.55)', borderRadius: [2, 2, 0, 0] }
            }
          ]
        };
      });

      // —— 流量趋势：PV / UV 折线 ——
      bindChart(function () { return trafficEl.value; }, function () {
        var rows = traffic.value;
        if (!rows.length) return null;
        var labels = rows.map(function (r) { return shortTime(r.window_start); });
        return {
          color: [C.pv, C.uv],
          tooltip: Object.assign({ trigger: 'axis' }, COMMON_TIP),
          legend: { data: ['PV', 'UV'], right: 8, top: 0, textStyle: { color: C.axis, fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
          grid: { left: 8, right: 8, top: 40, bottom: 4, containLabel: true },
          xAxis: timeAxis(labels),
          yAxis: [intAxis()],
          series: [
            makeLine('PV', rows.map(function (r) { return numberOrNull(r.pv); }), C.pv, 0),
            makeLine('UV', rows.map(function (r) { return numberOrNull(r.uv); }), C.uv, 0)
          ]
        };
      });

      // —— 类目 Top5：横向条形图（GMV 降序，ECharts 类目轴自下而上，故需反转） ——
      bindChart(function () { return categoryEl.value; }, function () {
        var rows = categoryTop.value.slice(0, 5);
        if (!rows.length) return null;
        var ordered = rows.slice().reverse();
        return {
          color: [C.accent],
          tooltip: Object.assign({
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            valueFormatter: function (v) { return formatMoney(v) + ' 元'; }
          }, COMMON_TIP),
          grid: { left: 8, right: 56, top: 12, bottom: 4, containLabel: true },
          xAxis: moneyAxis(),
          yAxis: makeAxis('category', {
            data: ordered.map(function (r) { return r.category_name; }),
            splitLine: { show: false },
            axisLabel: { color: '#c9d1d9', fontSize: 12 }
          }),
          series: [{
            name: 'GMV',
            type: 'bar',
            data: ordered.map(function (r) { return numberOrNull(r.gmv); }),
            barMaxWidth: 18,
            itemStyle: { color: C.accent, borderRadius: [0, 3, 3, 0] },
            label: {
              show: true, position: 'right', color: '#c9d1d9', fontSize: 11,
              formatter: function (p) { return formatMoney(p.value); }
            }
          }]
        };
      });

      return {
        loading: panel.loading,
        error: panel.error,
        overview: overview,
        trade: trade,
        traffic: traffic,
        categoryTop: categoryTop,
        kpiCards: kpiCards,
        tradeEl: tradeEl,
        trafficEl: trafficEl,
        categoryEl: categoryEl,
        fmtMoney: formatMoney,
        fmtInt: formatInt,
        fmtTime: formatTime
      };
    }
  };

  // ============================================================
  // ---------- 页面 2：交易分析 ----------
  // ============================================================

  var TradePanel = {
    name: 'TradePanel',
    template: '#tpl-trade',
    setup: function () {
      var panel = usePanel();
      var series = ref([]);
      var page = ref(1);
      var chartEl = ref(null);

      // 最近 200 个窗口（后端一次最多返回 200 行，超出部分不在这里拼页请求）
      var WINDOW_LIMIT = 200;

      function load() {
        return panel.run(function () {
          return api.trade(WINDOW_LIMIT).then(function (rows) {
            series.value = rows || [];
            page.value = 1;
            return series.value;
          });
        }, function () { return []; });
      }
      panel.setReload(load);
      onMounted(load);

      // 接口按 window_start 升序返回（便于画折线），表格展示则倒序更符合阅读习惯
      var rowsDesc = computed(function () { return series.value.slice().reverse(); });
      var pageCount = computed(function () {
        return Math.max(1, Math.ceil(rowsDesc.value.length / PAGE_SIZE));
      });
      var pagedRows = computed(function () {
        var start = (page.value - 1) * PAGE_SIZE;
        return rowsDesc.value.slice(start, start + PAGE_SIZE);
      });
      watch(pageCount, function (total) {
        if (page.value > total) page.value = total;
      });

      // 客单价等比率一律用"整数分 ÷ 整数计数"重算，不用后端逐窗口值取平均，
      // 也不做浮点累加：这样与 sql/metadata/metrics.md 的口径完全一致。
      var totals = computed(function () {
        var rows = series.value;
        var gmvCents = sumCents(rows.map(function (r) { return r.gmv; }));
        var payCents = sumCents(rows.map(function (r) { return r.payment_amount; }));
        var refundCents = sumCents(rows.map(function (r) { return r.refund_amount; }));
        var orders = 0, payCnt = 0, payFail = 0, refundCnt = 0;
        rows.forEach(function (r) {
          orders += numberOrNull(r.order_cnt) || 0;
          payCnt += numberOrNull(r.payment_cnt) || 0;
          payFail += numberOrNull(r.payment_fail_cnt) || 0;
          refundCnt += numberOrNull(r.refund_cnt) || 0;
        });
        return {
          gmvCents: gmvCents,
          payCents: payCents,
          refundCents: refundCents,
          orders: orders,
          payCnt: payCnt,
          payFail: payFail,
          refundCnt: refundCnt
        };
      });

      var cards = computed(function () {
        var t = totals.value;
        var avgCents = (t.gmvCents !== null && t.orders > 0) ? (t.gmvCents / BigInt(t.orders)) : null;
        var successRate = (t.payCnt + t.payFail) > 0 ? (t.payCnt / (t.payCnt + t.payFail)) : null;
        // 退款率同样避开大额浮点：先在"整数分"上算出万分比，再换算成小数
        var refundRate = (t.refundCents !== null && t.payCents !== null && t.payCents > 0n)
          ? Number((t.refundCents * 10000n) / t.payCents) / 10000 : null;
        var span = series.value.length
          ? shortTime(series.value[0].window_start) + ' ~ ' + shortTime(series.value[series.value.length - 1].window_start)
          : '暂无窗口';

        return [
          { label: 'GMV 合计', value: formatCents(t.gmvCents), unit: '元', sub: span, tip: definitionOf('gmv') },
          { label: '客单价', value: formatCents(avgCents), unit: '元', sub: 'GMV ÷ 订单量 ' + formatInt(t.orders) + ' 笔', tip: definitionOf('avg_order_amount') },
          { label: '支付成功率', value: formatRate(successRate), unit: '', sub: '成功 ' + formatInt(t.payCnt) + ' / 失败 ' + formatInt(t.payFail), tip: definitionOf('payment_success_rate') },
          { label: '退款率（金额口径）', value: formatRate(refundRate), unit: '', sub: '退款 ' + formatCents(t.refundCents) + ' / 支付 ' + formatCents(t.payCents), tip: definitionOf('refund_rate') }
        ];
      });

      // —— 双轴折线：GMV（左轴）+ 支付金额（右轴） ——
      bindChart(function () { return chartEl.value; }, function () {
        var rows = series.value;
        if (!rows.length) return null;
        var labels = rows.map(function (r) { return shortTime(r.window_start); });
        return {
          color: [C.gmv, C.pay],
          tooltip: Object.assign({
            trigger: 'axis',
            axisPointer: { type: 'line', lineStyle: { color: C.grid } },
            valueFormatter: function (v) { return formatMoney(v) + ' 元'; }
          }, COMMON_TIP),
          legend: { data: ['GMV', '支付金额'], right: 8, top: 0, textStyle: { color: C.axis, fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
          grid: { left: 8, right: 8, top: 40, bottom: 4, containLabel: true },
          xAxis: timeAxis(labels),
          yAxis: [moneyAxis(), moneyAxis()],
          series: [
            makeLine('GMV', rows.map(function (r) { return numberOrNull(r.gmv); }), C.gmv, 0),
            makeLine('支付金额', rows.map(function (r) { return numberOrNull(r.payment_amount); }), C.pay, 1)
          ]
        };
      });

      return {
        loading: panel.loading,
        error: panel.error,
        series: series,
        chartEl: chartEl,
        cards: cards,
        page: page,
        pageCount: pageCount,
        pagedRows: pagedRows,
        fmtMoney: formatMoney,
        fmtInt: formatInt,
        fmtRate: formatRate,
        fmtTime: formatTime
      };
    }
  };

  // ============================================================
  // ---------- 页面 3：流量分析 ----------
  // ============================================================

  var TrafficPanel = {
    name: 'TrafficPanel',
    template: '#tpl-traffic',
    setup: function () {
      var panel = usePanel();
      var series = ref([]);
      var chartEl = ref(null);
      var funnelEl = ref(null);

      function load() {
        return panel.run(function () {
          // 漏斗与趋势共用同一批窗口数据，避免两个接口口径错位
          return Promise.all([api.traffic(60), api.funnel(60)]).then(function (res) {
            series.value = res[0] || [];
            return series.value;
          });
        }, function () { return []; });
      }
      panel.setReload(load);
      onMounted(load);

      // 漏斗层级以"所选窗口累计"重算，不用后端返回的窗口均值，
      // 保证 view > click > cart > buy 的单调性与 metrics.md 的公式一致。
      var funnelTotal = computed(function () {
        var t = { view_cnt: 0, click_cnt: 0, cart_cnt: 0, buy_cnt: 0 };
        series.value.forEach(function (r) {
          t.view_cnt += numberOrNull(r.view_cnt) || 0;
          t.click_cnt += numberOrNull(r.click_cnt) || 0;
          t.cart_cnt += numberOrNull(r.cart_cnt) || 0;
          t.buy_cnt += numberOrNull(r.buy_cnt) || 0;
        });
        return t;
      });

      var sizeTotals = computed(function () {
        var uv = 0, pv = 0, favorite = 0;
        series.value.forEach(function (r) {
          // UV 是去重指标，逐窗口相加会重复计数：这里只做"窗口峰值"展示更诚实
          uv = Math.max(uv, numberOrNull(r.uv) || 0);
          pv += numberOrNull(r.pv) || 0;
          favorite += numberOrNull(r.favorite_cnt) || 0;
        });
        return { uv: uv, pv: pv, favorite: favorite };
      });

      var sizeCards = computed(function () {
        var s = sizeTotals.value;
        var f = funnelTotal.value;
        return [
          { label: 'UV（单窗口峰值）', value: formatInt(s.uv), unit: '人', sub: '去重用户不可跨窗口相加', tip: definitionOf('uv') },
          { label: 'PV 合计', value: formatInt(s.pv), unit: '次', sub: '全部行为事件数', tip: definitionOf('pv') },
          { label: '浏览次数', value: formatInt(f.view_cnt), unit: '次', sub: '漏斗第一层', tip: definitionOf('view_cnt') },
          { label: '购买次数', value: formatInt(f.buy_cnt), unit: '次', sub: '收藏 ' + formatInt(s.favorite) + ' 次', tip: definitionOf('buy_cnt') }
        ];
      });

      var rateCards = computed(function () {
        var f = funnelTotal.value;
        return [
          { label: '点击率', value: formatRate(ratioOf(f.click_cnt, f.view_cnt)), unit: '', sub: '点击 ÷ 浏览', tip: definitionOf('click_rate') },
          { label: '加购率', value: formatRate(ratioOf(f.cart_cnt, f.click_cnt)), unit: '', sub: '加购 ÷ 点击', tip: definitionOf('cart_rate') },
          { label: '购买转化率', value: formatRate(ratioOf(f.buy_cnt, f.cart_cnt)), unit: '', sub: '购买 ÷ 加购', tip: definitionOf('buy_rate') },
          { label: '整体转化率', value: formatRate(ratioOf(f.buy_cnt, f.view_cnt)), unit: '', sub: '购买 ÷ 浏览（端到端）', tip: '端到端转化率 = buy_cnt / view_cnt，用于衡量整条链路的漏损。' }
        ];
      });

      var steps = computed(function () {
        var f = funnelTotal.value;
        var pairs = [
          { label: '浏览 → 点击', from: f.view_cnt, to: f.click_cnt, note: '流失 ' + formatInt(f.view_cnt - f.click_cnt) },
          { label: '点击 → 加购', from: f.click_cnt, to: f.cart_cnt, note: '流失 ' + formatInt(f.click_cnt - f.cart_cnt) },
          { label: '加购 → 购买', from: f.cart_cnt, to: f.buy_cnt, note: '流失 ' + formatInt(f.cart_cnt - f.buy_cnt) }
        ];
        return pairs.map(function (p) {
          return { label: p.label, value: formatRate(ratioOf(p.to, p.from)), note: p.note };
        });
      });

      // —— 漏斗图：使用 ECharts funnel，标签显示层名与累计值 ——
      bindChart(function () { return funnelEl.value; }, function () {
        var f = funnelTotal.value;
        if (!f.view_cnt) return null;
        return {
          color: [C.series[0], C.series[5], C.series[2], C.series[1]],
          tooltip: Object.assign({
            trigger: 'item',
            formatter: function (p) {
              var total = funnelTotal.value.view_cnt;
              var share = total ? ((p.value / total) * 100).toFixed(2) + '%' : '—';
              return p.name + '<br/>数量：' + formatInt(p.value) + '<br/>占浏览：' + share;
            }
          }, COMMON_TIP),
          series: [{
            type: 'funnel',
            left: '6%',
            right: '6%',
            top: 16,
            bottom: 16,
            minSize: '28%',
            sort: 'descending',
            gap: 2,
            label: { color: '#c9d1d9', fontSize: 12, formatter: '{b}  {c}' },
            labelLine: { length: 12, lineStyle: { color: C.grid } },
            itemStyle: { borderColor: 'transparent', borderWidth: 0, opacity: 0.92 },
            emphasis: { label: { color: '#ffffff', fontWeight: 'bold' } },
            data: [
              { name: '浏览', value: f.view_cnt },
              { name: '点击', value: f.click_cnt },
              { name: '加购', value: f.cart_cnt },
              { name: '购买', value: f.buy_cnt }
            ]
          }]
        };
      });

      // —— 趋势图：PV / UV / 浏览次数 ——
      bindChart(function () { return chartEl.value; }, function () {
        var rows = series.value;
        if (!rows.length) return null;
        var labels = rows.map(function (r) { return shortTime(r.window_start); });
        return {
          color: [C.pv, C.uv, C.view],
          tooltip: Object.assign({ trigger: 'axis' }, COMMON_TIP),
          legend: { data: ['PV', 'UV', '浏览次数'], right: 8, top: 0, textStyle: { color: C.axis, fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
          grid: { left: 8, right: 8, top: 40, bottom: 4, containLabel: true },
          xAxis: timeAxis(labels),
          yAxis: [intAxis()],
          series: [
            makeLine('PV', rows.map(function (r) { return numberOrNull(r.pv); }), C.pv, 0),
            makeLine('UV', rows.map(function (r) { return numberOrNull(r.uv); }), C.uv, 0),
            makeLine('浏览次数', rows.map(function (r) { return numberOrNull(r.view_cnt); }), C.view, 0)
          ]
        };
      });

      return {
        loading: panel.loading,
        error: panel.error,
        series: series,
        funnelTotal: funnelTotal,
        sizeCards: sizeCards,
        rateCards: rateCards,
        steps: steps,
        chartEl: chartEl,
        funnelEl: funnelEl,
        fmtInt: formatInt
      };
    }
  };

  // ============================================================
  // ---------- 页面 4：类目销售 ----------
  // ============================================================

  var CategoryPanel = {
    name: 'CategoryPanel',
    template: '#tpl-category',
    setup: function () {
      var panel = usePanel();
      var rows = ref([]);
      var windowLimit = ref(60);
      var topN = ref(10);
      var barEl = ref(null);
      var pieEl = ref(null);

      function load() {
        var limit = clamp(topN.value, 3, 50);
        return panel.run(function () {
          return api.category(limit, windowLimit.value).then(function (list) {
            rows.value = list || [];
            return rows.value;
          });
        }, function () { return []; });
      }
      panel.setReload(load);
      onMounted(load);

      // 「最近 N 分钟 / 全部」切换与条数变化都要重新请求：window_limit 是后端聚合范围，
      // 不是前端截断，因此必须走接口而不是本地过滤。
      function setWindow(minutes) {
        if (windowLimit.value === minutes) return;
        windowLimit.value = minutes;
        load();
      }

      watch(topN, function () {
        // 输入框可能瞬时为空，clamp 后再决定是否请求，避免发出 limit=NaN
        var limit = clamp(topN.value, 3, 50);
        if (limit !== topN.value) topN.value = limit;
        load();
      });

      // 后端已按 GMV 降序返回，这里只做一次防御性排序，保证图表与表格顺序一致
      var list = computed(function () {
        return rows.value.slice().sort(function (a, b) {
          var ca = moneyToCents(a.gmv);
          var cb = moneyToCents(b.gmv);
          if (ca === null) return 1;
          if (cb === null) return -1;
          return cb > ca ? 1 : (cb < ca ? -1 : 0);
        });
      });

      var totalGmvCents = computed(function () {
        return sumCents(list.value.map(function (r) { return r.gmv; }));
      });
      var totalGmv = computed(function () { return formatCents(totalGmvCents.value); });
      var totalOrders = computed(function () {
        var n = 0;
        list.value.forEach(function (r) { n += numberOrNull(r.order_cnt) || 0; });
        return formatInt(n);
      });

      // —— 横向条形：排行（类目轴自下而上，反转后最大值显示在最上方） ——
      bindChart(function () { return barEl.value; }, function () {
        var items = list.value;
        if (!items.length) return null;
        var ordered = items.slice().reverse();
        return {
          color: [C.series[0]],
          tooltip: Object.assign({
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            formatter: function (params) {
              var p = params[0];
              var row = ordered[p.dataIndex] || {};
              return row.category_name + '<br/>GMV：' + formatMoney(row.gmv) + ' 元' +
                '<br/>订单量：' + formatInt(row.order_cnt) + ' 笔' +
                '<br/>客单价：' + formatMoney(row.avg_order_amount) + ' 元';
            }
          }, COMMON_TIP),
          grid: { left: 8, right: 80, top: 12, bottom: 4, containLabel: true },
          xAxis: moneyAxis(),
          yAxis: makeAxis('category', {
            data: ordered.map(function (r) { return r.category_name; }),
            splitLine: { show: false },
            axisLabel: { color: '#c9d1d9', fontSize: 12 }
          }),
          series: [{
            name: 'GMV',
            type: 'bar',
            data: ordered.map(function (r) { return numberOrNull(r.gmv); }),
            barMaxWidth: 18,
            itemStyle: { color: C.series[0], borderRadius: [0, 3, 3, 0] },
            label: {
              show: true, position: 'right', color: '#c9d1d9', fontSize: 11,
              formatter: function (p) { return formatMoney(p.value); }
            }
          }]
        };
      });

      // —— 占比饼图：用环形展示，避免大屏正中大面积实色 ——
      bindChart(function () { return pieEl.value; }, function () {
        var items = list.value;
        if (!items.length) return null;
        return {
          color: C.series,
          tooltip: Object.assign({
            trigger: 'item',
            formatter: function (p) {
              return p.name + '<br/>GMV：' + formatMoney(p.value) + ' 元<br/>占比：' + p.percent + '%';
            }
          }, COMMON_TIP),
          legend: { bottom: 0, left: 'center', textStyle: { color: C.axis, fontSize: 11 }, itemWidth: 12, itemHeight: 8 },
          series: [{
            type: 'pie',
            radius: ['42%', '68%'],
            center: ['50%', '44%'],
            avoidLabelOverlap: true,
            itemStyle: { borderColor: '#0f172a', borderWidth: 2 },
            label: { color: '#c9d1d9', fontSize: 11, formatter: '{b} {d}%' },
            labelLine: { length: 8, length2: 8, lineStyle: { color: C.grid } },
            data: items.map(function (r) {
              return { name: r.category_name, value: numberOrNull(r.gmv) };
            })
          }]
        };
      });

      return {
        loading: panel.loading,
        error: panel.error,
        rows: rows,
        list: list,
        windowLimit: windowLimit,
        topN: topN,
        ALL_WINDOWS: ALL_WINDOWS,
        setWindow: setWindow,
        totalGmv: totalGmv,
        totalOrders: totalOrders,
        barEl: barEl,
        pieEl: pieEl,
        fmtMoney: formatMoney,
        fmtInt: formatInt
      };
    }
  };

  // ============================================================
  // ---------- 页面 5：订单明细 ----------
  // ============================================================

  var OrdersPanel = {
    name: 'OrdersPanel',
    template: '#tpl-orders',
    setup: function () {
      var panel = usePanel();
      var result = ref(null);
      var category = ref('');
      var startDate = ref('');
      var endDate = ref('');
      var offset = ref(0);
      var categoryOptions = ref([]);

      var detailOpen = ref(false);
      var detailId = ref('');
      var detail = ref(null);
      var detailLoading = ref(false);
      var detailError = ref('');

      var page = computed(function () { return Math.floor(offset.value / PAGE_SIZE) + 1; });
      var pageCount = computed(function () {
        var total = result.value ? numberOrNull(result.value.total) || 0 : 0;
        return Math.max(1, Math.ceil(total / PAGE_SIZE));
      });

      var filterSummary = computed(function () {
        var parts = [];
        if (category.value) parts.push('类目：' + category.value);
        if (startDate.value) parts.push('开始日期 ≥ ' + startDate.value);
        if (endDate.value) parts.push('结束日期 ≤ ' + endDate.value);
        return parts.length ? '当前筛选：' + parts.join('　|　') : '';
      });

      function currentParams() {
        var params = { limit: PAGE_SIZE, offset: offset.value };
        if (category.value) params.category = category.value;
        if (startDate.value) params.start = startDate.value;
        if (endDate.value) params.end = endDate.value;
        return params;
      }

      function load() {
        return panel.run(function () {
          return api.orders(currentParams()).then(function (data) {
            // 防御：接口在无数据时可能省略 items，统一补成空数组避免模板报错
            var payload = data || {};
            payload.items = payload.items || [];
            result.value = payload;
            return payload;
          });
        }, function () { return null; });
      }
      panel.setReload(load);

      onMounted(function () {
        load();
        // 类目下拉的候选项来自类目聚合接口；失败时只保留「全部类目」，不影响筛选功能
        api.category(50, ALL_WINDOWS).then(function (rows) {
          categoryOptions.value = (rows || []).map(function (r) { return r.category_name; }).filter(Boolean);
        }).catch(function () {
          categoryOptions.value = [];
        });
      });

      function applyFilters() {
        offset.value = 0;
        load();
      }

      function resetFilters() {
        category.value = '';
        startDate.value = '';
        endDate.value = '';
        offset.value = 0;
        load();
      }

      function turnPage(step) {
        var next = offset.value + step * PAGE_SIZE;
        if (next < 0) return;
        var total = result.value ? numberOrNull(result.value.total) || 0 : 0;
        if (next >= total && step > 0) return;
        offset.value = next;
        load();
      }

      function openDetail(orderId) {
        if (detailLoading.value) return;
        detailOpen.value = true;
        detailId.value = orderId;
        detail.value = null;
        detailError.value = '';
        detailLoading.value = true;
        // 详情失败只在弹窗内提示，不打红整页——列表本身仍然是可用的
        api.orderDetail(orderId).then(function (data) {
          detail.value = {
            order: (data && data.order) || {},
            payments: (data && data.payments) || [],
            refunds: (data && data.refunds) || []
          };
        }).catch(function (cause) {
          detailError.value = (cause && cause.message) || '订单详情加载失败';
        }).then(function () {
          detailLoading.value = false;
        });
      }

      function closeDetail() {
        detailOpen.value = false;
        detail.value = null;
        detailError.value = '';
      }

      function onEsc(event) {
        if (event.key === 'Escape' && detailOpen.value) closeDetail();
      }
      onMounted(function () { document.addEventListener('keydown', onEsc); });
      onBeforeUnmount(function () { document.removeEventListener('keydown', onEsc); });

      return {
        loading: panel.loading,
        error: panel.error,
        result: result,
        category: category,
        startDate: startDate,
        endDate: endDate,
        categoryOptions: categoryOptions,
        filterSummary: filterSummary,
        page: page,
        pageCount: pageCount,
        offset: offset,
        limit: PAGE_SIZE,
        applyFilters: applyFilters,
        resetFilters: resetFilters,
        turnPage: turnPage,
        detailOpen: detailOpen,
        detailId: detailId,
        detail: detail,
        detailLoading: detailLoading,
        detailError: detailError,
        openDetail: openDetail,
        closeDetail: closeDetail,
        fmtMoney: formatMoney,
        fmtInt: formatInt,
        fmtTime: formatTime
      };
    }
  };

  // ============================================================
  // ---------- 页面 6：指标口径 ----------
  // ============================================================

  var MetricsPanel = {
    name: 'MetricsPanel',
    template: '#tpl-metrics',
    setup: function () {
      var panel = usePanel();
      var metrics = ref([]);
      var tables = ref([]);
      var keyword = ref('');
      // 口径文档的版本与更新时间：来自 /meta/metrics（文档同源，必须显示出来）
      var docVersion = ref('');
      var docUpdatedAt = ref('');
      var openTables = reactive({});

      function load() {
        return panel.run(function () {
          return Promise.all([api.metaMetrics(), api.metaTables()]).then(function (res) {
            var payload = res[0] || {};
            metrics.value = metricList(payload);
            docVersion.value = payload.version || '';
            docUpdatedAt.value = payload.updated_at || '';
            tables.value = res[1] || [];
            return metrics.value;
          });
        }, function () { return []; });
      }
      panel.setReload(load);
      onMounted(load);

      // 本地过滤而非重新请求：指标字典体量很小，本地筛选响应更快
      var filteredMetrics = computed(function () {
        var kw = keyword.value.trim().toLowerCase();
        if (!kw) return metrics.value;
        return metrics.value.filter(function (item) {
          var haystack = [item.domain, item.metric, item.field, item.table, item.definition]
            .join(' ').toLowerCase();
          return haystack.indexOf(kw) >= 0;
        });
      });

      function isOpen(name) { return openTables[name] === true; }
      function toggle(name) { openTables[name] = !isOpen(name); }

      var allOpen = computed(function () {
        if (!tables.value.length) return false;
        return tables.value.every(function (t) { return isOpen(t.table); });
      });

      function toggleAll() {
        var next = !allOpen.value;
        tables.value.forEach(function (t) { openTables[t.table] = next; });
      }

      return {
        loading: panel.loading,
        error: panel.error,
        metrics: metrics,
        tables: tables,
        keyword: keyword,
        docVersion: docVersion,
        docUpdatedAt: docUpdatedAt,
        filteredMetrics: filteredMetrics,
        isOpen: isOpen,
        toggle: toggle,
        allOpen: allOpen,
        toggleAll: toggleAll
      };
    }
  };

  // ============================================================
  // ---------- 根组件 ----------
  // ============================================================

  var App = {
    name: 'App',
    setup: function () {
      var page = currentPage;
      var collapsed = ref(false);
      setupRoute();

      var route = computed(function () {
        var key = page.value;
        for (var i = 0; i < ROUTES.length; i++) {
          if (ROUTES[i].key === key) return ROUTES[i];
        }
        return ROUTES[0];
      });

      var anyLoading = computed(function () { return refreshing.value > 0; });
      var updatedText = computed(function () { return latestStamp.value || '—'; });
      var errorText = computed(function () { return globalError.value; });

      // 刷新：逐个调用已登记的面板 reload；面板内部有 loading 互斥，重复点击无副作用
      function refreshAll() {
        globalError.value = '';
        var jobs = reloadHooks.map(function (fn) { return fn(); });
        Promise.all(jobs).catch(function () {
          // 面板各自已经记录了错误信息，这里只需吞掉聚合异常
          return null;
        });
      }

      onMounted(function () { preloadDefinitions(); });

      return {
        routes: ROUTES,
        page: page,
        route: route,
        collapsed: collapsed,
        anyLoading: anyLoading,
        updatedText: updatedText,
        errorText: errorText,
        refreshAll: refreshAll
      };
    }
  };

  // 根模板直接取 #app 容器的现有 innerHTML（index.html 中就是页面骨架）。
  // 说明：Vue 的 createApp 不会自动把挂载容器的内容当成模板，
  // 因此这里显式读取一次；容器上的 v-cloak 不参与渲染，先移除避免残留在 DOM 上。
  var appHost = document.getElementById('app');
  var appTemplate = appHost ? appHost.innerHTML : '';
  if (appHost) appHost.removeAttribute('v-cloak');

  var app = createApp(Object.assign({ template: appTemplate }, App));
  app.component('icon', Icon);
  app.component('skeleton', Skeleton);
  app.component('empty', EmptyState);
  app.component('kpi', KpiCard);
  app.component('select-box', SelectBox);
  app.component('overview-panel', OverviewPanel);
  app.component('trade-panel', TradePanel);
  app.component('traffic-panel', TrafficPanel);
  app.component('category-panel', CategoryPanel);
  app.component('orders-panel', OrdersPanel);
  app.component('metrics-panel', MetricsPanel);
  app.mount('#app');

  if (bootEl && bootEl.parentNode) bootEl.parentNode.removeChild(bootEl);
})();
