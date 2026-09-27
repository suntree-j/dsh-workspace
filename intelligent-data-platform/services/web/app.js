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
 *
 * 5. 图表样式（网格线 / 坐标轴 / 图例 / 提示框 / 调色板）集中在 CHART_THEME，
 *    六个页面只描述"画什么"，不再各写一套颜色与描边 —— 与 styles.css 的
 *    设计令牌一一对应（同一含义不出现两种色值）。
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
  // h：创建 vnode。Ask 页的安全 Markdown 渲染用它 ——
  // 全程不碰 innerHTML，模型输出里的 HTML 标签天然不会被解析成元素。
  var h = Vue.h;

  // ============================================================
  // ---------- 常量与元信息 ----------
  // ============================================================

  // 由 index.html 的 <meta name="api-base"> 提供；
  // 取不到时回退到相对路径 ./api，保证在任意子路径下都能工作（不拼接域名）。
  //
  // !! 这个值是**绝对路径**（部署在 /data/ 下时为 "/data/api"），
  //    不是"前缀" —— 曾经把它当相对前缀再拼一次，得到
  //    /data/api/api/health 这种路径，整站所有请求 404（实测踩坑）。
  //    因此下面显式定义两个完整基址，谁都不再二次拼接。!!
  var API_BASE = (function () {
    var meta = document.querySelector('meta[name="api-base"]');
    var value = meta && meta.getAttribute('content');
    return (value && value.trim()) || './api';
  })();

  // 数据问答 Agent 的基址（Sprint 7），与数据服务同一套 Nginx 反代。
  // 从 API_BASE 推导：/data/api → /data/agent；相对形式 ./api → ./agent。
  var AGENT_BASE = (function () {
    var base = API_BASE.replace(/\/+$/, '');
    if (/\/api$/.test(base)) return base.slice(0, -3) + 'agent';
    return base + '/agent';
  })();

  // 与 styles.css 的设计令牌对应：深色底、低饱和、高区分度
  var C = {
    text: '#eef3f9',
    text2: '#c3cddb',
    axis: '#94a2b6',                                        // --text-3
    grid: 'rgba(148,163,184,0.12)',                         // --line-subtle
    surface: '#101725',                                     // --surface-1（环形图缝隙）
    trade: '#5b9cff',                                       // --domain-trade
    traffic: '#22c9b6',                                     // --domain-traffic
    category: '#a884ff',                                    // --domain-category
    pos: '#46c46a',                                         // --pos
    neg: '#ff7a72',                                         // --neg
    warn: '#e2b04a',                                        // --warning
    neutral: '#8b9bb4',
    series: ['#5b9cff', '#22c9b6', '#a884ff', '#e2b04a', '#ff7a72', '#4fd1e0', '#f0883e', '#8b9bb4']
  };

  // 类目页「全部」选项：窗口上限。
  //
  // !! 这里曾经写 1000000，是一个**真实存在的线上缺陷**（Sprint 13 前端收口时发现）!!
  //   后端契约是 `window_limit: int = Query(0, ge=0, le=20160)` ——
  //   上限 20160（= 14 天 × 1440 分钟），且**用 0 表示"全量口径"**。
  //   传 1000000 会在 FastAPI 的参数校验层就被拒：
  //     GET /metrics/category?limit=50&window_limit=1000000 → 422 Unprocessable Entity
  //   后果并不显眼，所以一直没被发现：
  //     - 类目页选「全部」时请求失败，页面走错误分支而不是崩溃；
  //     - 订单页的"类目候选项"拉取失败后被 `.catch` 静默兜底成空数组，
  //       表现只是"类目下拉里没有可选项"——容易被当成"数据就是这样"。
  //   修法：改用后端自己的哨兵值 0。**不要**改成 20160 ——
  //   那会悄悄把"全部"变成"最近 14 天"，是语义降级。
  var ALL_WINDOWS = 0;
  var PAGE_SIZE = 20;
  var TRADE_WINDOW_LIMIT = 200;

  // 顶栏数据源标识：由接口信封的 source 字段刷新（见 apiGet），
  // 未拿到时显示启动文案而不是空白。
  var sourceLabel = ref('实时链路');
  var sourceDetail = ref('');

  // ============================================================
  // ---------- ECharts 主题（集中一处配置） ----------
  // ============================================================
  // 六个页面共享同一套网格线 / 坐标轴 / 图例 / 提示框样式：
  //   - 去掉 ECharts 默认的粗重描边与高饱和配色；
  //   - 网格线用极低对比虚线，坐标轴线基本隐去，让数据本身成为主角；
  //   - 提示框与下拉、弹窗共用同一表面色与圆角，视觉上"同一个产品"。
  var AXIS_LABEL = { color: C.axis, fontSize: 11 };
  var CHART_THEME = {
    backgroundColor: 'transparent',
    animation: false,             // 数据看板以"读数"为目的，动画只干扰刷新时的对比
    textStyle: { color: C.text2, fontSize: 12 },
    color: C.series,
    grid: { left: 10, right: 14, top: 42, bottom: 6, containLabel: true },
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: '#1b2739',
      borderColor: 'rgba(148,163,184,0.26)',
      borderWidth: 1,
      padding: [10, 12],
      textStyle: { color: C.text, fontSize: 12 },
      extraCssText: 'box-shadow:0 20px 48px rgba(3,6,12,0.6);border-radius:10px;'
    },
    legend: {
      right: 4,
      top: 0,
      icon: 'roundRect',
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 14,
      textStyle: { color: C.axis, fontSize: 11 }
    }
  };

  function merge(target, source) {
    var out = {};
    var key;
    for (key in target) { if (Object.prototype.hasOwnProperty.call(target, key)) out[key] = target[key]; }
    for (key in source) {
      if (!Object.prototype.hasOwnProperty.call(source, key)) continue;
      out[key] = (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key]) &&
        target[key] && typeof target[key] === 'object' && !Array.isArray(target[key]))
        ? merge(target[key], source[key])
        : source[key];
    }
    return out;
  }

  // 坐标轴工厂：value 轴显示极浅虚线网格，category 轴不要网格
  function makeAxis(kind, opts) {
    var base = {
      type: kind,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: Object.assign({}, AXIS_LABEL),
      splitLine: kind === 'value'
        ? { show: true, lineStyle: { color: C.grid, type: 'dashed', width: 1 } }
        : { show: false }
    };
    return merge(base, opts || {});
  }

  // 折线：细描边 + 圆头，配一层自上而下渐隐的面积色（不用动画，静态也成立）
  function makeLine(name, data, color, yIndex) {
    var smooth = data.filter(function (v) { return v !== null && v !== undefined; }).length >= 5;
    return {
      name: name,
      type: 'line',
      yAxisIndex: yIndex || 0,
      data: data,
      smooth: smooth,
      smoothMonotone: 'x',
      showSymbol: false,
      symbol: 'circle',
      symbolSize: 5,
      connectNulls: false,
      lineStyle: { width: 1.8, color: color, cap: 'round', join: 'round' },
      itemStyle: { color: color, borderColor: C.surface, borderWidth: 1 },
      areaStyle: {
        // 面积只做"提示量级"的辅助，透明度压到很低：
        // 否则单窗口尖峰会把整片绘图区涂满，反而看不清折线本身。
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: withAlpha(color, 0.18) },
          { offset: 1, color: withAlpha(color, 0.02) }
        ])
      },
      emphasis: { focus: 'series', scale: 1.2 }
    };
  }

  // 柱状：圆角 + 收窄宽度（深色大屏上细柱比粗柱更"轻"）
  function makeBar(name, data, color, opts) {
    var base = {
      name: name,
      type: 'bar',
      data: data,
      barMaxWidth: 16,
      barCategoryGap: '55%',
      itemStyle: {
        color: color,
        borderRadius: [3, 3, 0, 0],
        opacity: 0.92
      },
      emphasis: { itemStyle: { opacity: 1 }, focus: 'series' }
    };
    return merge(base, opts || {});
  }

  // 横向条形（类目排行）：右侧留白给数值标签，四角圆角只保留外端
  function makeRankBar(name, data, color) {
    return {
      name: name,
      type: 'bar',
      data: data,
      barMaxWidth: 18,
      itemStyle: {
        borderRadius: [0, 4, 4, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: withAlpha(color, 0.55) },
          { offset: 1, color: withAlpha(color, 1) }
        ])
      },
      emphasis: { focus: 'series' },
      label: {
        show: true,
        position: 'right',
        distance: 6,
        color: C.text2,
        fontSize: 11,
        formatter: function (p) { return formatMoney(p.value); }
      }
    };
  }

  // 类目轴（横向条形的 Y 轴）：名称靠右对齐，长名称自动截断，避免侵占绘图区
  function rankAxis(names) {
    return makeAxis('category', {
      data: names,
      axisLabel: {
        color: C.text2,
        fontSize: 12,
        width: 116,
        overflow: 'truncate',
        margin: 10
      }
    });
  }

  function timeAxis(labels) {
    return makeAxis('category', {
      data: labels,
      boundaryGap: true,
      axisLabel: Object.assign({}, AXIS_LABEL, { hideOverlap: true })
    });
  }

  // 金额轴刻度：只保留整数千分位。
  // 刻度标签带 ".00" 会挤掉绘图区宽度，而精确到分的信息在 tooltip 与表格里已有。
  function moneyAxis(extra) {
    return makeAxis('value', merge({
      splitNumber: 4,
      axisLabel: Object.assign({}, AXIS_LABEL, {
        formatter: function (v) { return formatIntGroup(v); }
      })
    }, extra || {}));
  }

  function intAxis(extra) {
    return makeAxis('value', merge({
      splitNumber: 4,
      axisLabel: Object.assign({}, AXIS_LABEL, {
        formatter: function (v) { return formatIntGroup(v); }
      })
    }, extra || {}));
  }

  // 把 #rrggbb + alpha 合成 rgba()，用于面积渐变与柱状渐变
  function withAlpha(hex, alpha) {
    var s = String(hex || '').replace('#', '');
    if (s.length === 3) s = s.charAt(0) + s.charAt(0) + s.charAt(1) + s.charAt(1) + s.charAt(2) + s.charAt(2);
    var n = parseInt(s, 16);
    if (!isFinite(n)) return hex;
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
  }

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

  // 千分位整数（不保留小数），用于坐标轴刻度这类"只需要量级感"的位置
  function formatIntGroup(v) {
    if (isMissing(v)) return '';
    var n = numberOrNull(v);
    if (n === null) return '';
    return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
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

  // 对账差异：后端返回的是字符串（Decimal 直接转字符串，避免浮点截断）。
  //   0 / 0.00 / -0.00 都算"无差异"，统一显示为 0 而不是 -0.00，
  //   否则一张全对的表里会冒出刺眼的 "-0.00"，看起来像有问题。
  function formatDelta(v) {
    if (isMissing(v)) return '—';
    var s = String(v).trim();
    var n = numberOrNull(s);
    if (n === null) return s;
    if (n === 0) return /\./.test(s) ? '0.00' : '0';
    return /\./.test(s) ? formatMoney(s) : String(n);
  }

  // 金额缩写：只用于图表坐标轴刻度（"12.3万"），表格里一律用完整数字。
  //   坐标轴要的是量级感，完整数字会把轴标签挤成一团。
  function compactMoney(v) {
    var n = numberOrNull(v);
    if (n === null) return '';
    var abs = Math.abs(n);
    if (abs >= 1e8) return (n / 1e8).toFixed(2) + '亿';
    if (abs >= 1e4) return (n / 1e4).toFixed(1) + '万';
    return formatIntGroup(n);
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

  /**
   * 环比增量描述：只比较"最近两个窗口"，且两个窗口都必须有非零取值。
   * 之所以加这道门槛：实时链路里最新窗口常常尚未写满（甚至为 0），
   * 直接算环比会得到 -100% 这种"看起来像事故"的假信号 —— 宁可显示"待下一窗口"。
   */
  function buildDelta(rows, field, mode, unit) {
    var list = rows || [];
    if (list.length < 2) return null;
    var last = numberOrNull(list[list.length - 1] && list[list.length - 1][field]);
    var prev = numberOrNull(list[list.length - 2] && list[list.length - 2][field]);
    if (last === null || prev === null) return null;
    if (isMissing(list[list.length - 1] && list[list.length - 1].window_start)) return null;
    if (last === 0 || prev === 0) {
      return {
        text: '待下一窗口',
        glyph: '·',
        tone: 'flat',
        // !! 用显式标记区分"真的持平"与"还没得比" !!
        //   两条路径的 tone 都是 'flat'，如果只靠 tone 判断，
        //   模板会把"0.0% 真持平"也一起降级成灰字。
        //   也刻意**不**用"文本里有没有 %"来判断 —— 那种判据会在改文案时静默失效。
        pending: true,
        a11y: '最近一个窗口取值为 0，暂不计算环比',
        title: '最近一个窗口取值为 0（实时链路可能尚未写满），因此不展示环比'
      };
    }
    var pct = ((last - prev) / Math.abs(prev)) * 100;
    var up = pct > 0.05;
    var down = pct < -0.05;
    var text = (up ? '+' : '') + pct.toFixed(1) + '%';
    var shown = (mode === 'money') ? formatMoney(last) : formatInt(last);
    return {
      text: text,
      glyph: up ? '▲' : (down ? '▼' : '—'),
      tone: up ? 'up' : (down ? 'down' : 'flat'),
      pending: false,
      a11y: '最近窗口较上一窗口' + (up ? '上升' : (down ? '下降' : '基本持平')) + Math.abs(pct).toFixed(1) + '%',
      title: '环比：' + formatTime(list[list.length - 1].window_start) + ' 窗口 ' + shown + (unit || '') +
        '，对比上一个窗口 ' + ((mode === 'money') ? formatMoney(prev) : formatInt(prev)) + (unit || '')
    };
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

  /**
   * 拼出请求 URL。
   *
   * 两个后端共用一套拼接逻辑，差别只在 base：
   *   API_BASE   → 只读数据服务 services/api（/data/api）
   *   AGENT_BASE → 数据问答 Agent services/agent（/data/agent）
   *
   * !! 这里只拼一次：base 已经是完整路径，调用方传**相对基址的路径**。
   *    绝不能在 base 上再叠 "/api" 之类的前缀 —— 那会拼出
   *    /data/api/api/health，整站 404（实测踩过）。!!
   */
  function buildUrl(base, path, params) {
    var url = String(base).replace(/\/+$/, '') + path;
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
   * 信封里的 source 字段是"数据来源说明"，各接口形态不一：
   *   - 字符串：直接就是说明文本；
   *   - 对象：{ tables, metric_definitions, time_range, note }（本项目后端实际返回）。
   * 看板必须能自证"这份数据来自哪张表、可不可信"，所以这里做兼容解析：
   * 顶栏徽标显示短标签（note），鼠标悬停给出涉及的 ADS 表清单。
   */
  // 顶栏数据源徽标的文案（Sprint 13 前端收口）
  //
  // !! 为什么不再直接把后端 source.note 当可见文案 !!
  //   note 是一整句工程说明（例如"只读账号 + SQL 守卫（仅 SELECT、强制 LIMIT）"），
  //   放在顶栏里有两个问题：
  //     ① 它是顶栏里唯一长度不可控的部分，会把"数据时间 + 刷新"挤走
  //        （实测 1280×800 下整页横向溢出 35px）；
  //     ② 审计指出它"像文档引用，不像数据源状态"。
  //   改成**固定短标签**，完整 note + 表清单全部保留在 title 提示里 ——
  //   信息一点没少，只是不再占主视线。
  //   `source.note` 仍然被读取（见下面的 detail），只是不作为主标签。
  function resolveSource(source) {
    if (typeof source === 'string') {
      var text = source.trim();
      return text ? { label: text, detail: '' } : null;
    }
    if (source && typeof source === 'object') {
      var note = typeof source.note === 'string' ? source.note.trim() : '';
      var tables = Array.isArray(source.tables) ? source.tables.filter(Boolean) : [];
      var bits = [];
      if (note) bits.push(note);
      if (tables.length) bits.push('数据表：' + tables.join('、'));
      var detail = bits.join('\n');
      if (!detail) return null;
      // 短标签：只表达"数据从哪来"，不表达"怎么保证安全"
      var label = tables.length ? ('只读数据服务 · ' + tables.length + ' 张表') : '只读数据服务';
      return { label: label, detail: detail };
    }
    return null;
  }

  /**
   * 统一 GET：拼 query → 解析信封 → 出错时抛出带中文 message 的 ApiError。
   * 返回信封中的 data 字段（业务代码不再关心信封结构）。
   */
  function apiGet(path, params, base) {
    var target = base || API_BASE;
    var init = { method: 'GET', headers: { Accept: 'application/json' }, cache: 'no-store' };
    return runRequest(buildUrl(target, path, params), init, target);
  }

  /**
   * 统一 POST（Sprint 7 起需要）。
   *
   * 为什么看板会出现 POST：
   *   数据问答 Agent 的提问内容与 Agent 的取数 SQL 都放在请求体里 ——
   *   问题与 SQL 都可能很长，塞进 URL 会撞上各级代理的长度限制，
   *   出现"短问题能问、复杂问题失败"这种最难定位的故障。
   *   注意：**取数仍然是只读的**，写操作在 SQL 守卫与 Doris 只读账号两层被拒。
   *
   * base 参数用于区分两个后端：
   *   '/api'   → 只读数据服务（services/api，8000）
   *   '/agent' → 数据问答 Agent（services/agent，8100）
   */
  function apiPost(path, payload, base) {
    var target = base || API_BASE;
    var init = {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify(payload || {})
    };
    return runRequest(buildUrl(target, path, null), init, target);
  }

  function runRequest(url, init, target) {
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
        var src = resolveSource(body.source);
        if (src) {
          sourceLabel.value = src.label;
          sourceDetail.value = src.detail;
        }
        return body.data;
      });
    }, function (cause) {
      // 网络层失败（后端未启动 / Nginx 未转发）与业务错误分开提示，便于定位
      var where = String(target).indexOf('agent') >= 0
        ? '无法连接到数据问答 Agent，请确认 data-platform-agent 服务与 Nginx 反向代理已启动'
        : '无法连接到后端数据接口，请确认 API 服务与 Nginx 反向代理已启动';
      throw new ApiError(where, 'NETWORK_ERROR', String((cause && cause.message) || cause), 0);
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
    metaTables: function () { return apiGet('/meta/tables'); },
    // 离线链路（Sprint 3）：批处理算出的同名同口径指标 + 批流对账结论
    batchOverview: function (days) { return apiGet('/batch/overview', { days: days }); },
    batchReconcile: function () { return apiGet('/batch/reconcile'); },
    // 数据问答 Agent（Sprint 7）：走独立的 AGENT_BASE（/data/agent）
    agentHealth: function () { return apiGet('/health', null, AGENT_BASE); },
    agentAsk: function (question) { return apiPost('/ask', { question: question }, AGENT_BASE); }
  };

  // ============================================================
  // ---------- 状态探针（Overview / 实时链路共用） ----------
  // ============================================================
  //
  // 为什么单独抽出来：状态芯片要出现在两个页面上，如果各写一份，
  // "对账 PASS 的判据"就会有两处实现 —— 日后改一处必然漂移。
  //
  // !! 三条探针必须**互不阻塞** !!
  //   FRONTEND_UI_AUDIT §16 明确列为中风险：
  //   "Overview 增加 reconcile/agent 请求致 502 放大"。
  //   所以用 allSettled 语义（每个自行 catch），任何一条失败只让
  //   **它自己那枚芯片**变灰，绝不影响 GMV 与其它芯片 ——
  //   状态芯片属于增强信息，不该有能力把主页打空。
  var MONITOR_HREF = (function () {
    var p = window.location.pathname.replace(/\/data\/.*$/, '/');
    return p + 'grafana/';
  })();

  function probeStatus() {
    function settle(promise, fallback) {
      return promise.then(
        function (v) { return { ok: true, value: v }; },
        function () { return { ok: false, value: fallback }; }
      );
    }
    return Promise.all([
      settle(api.health(), null),
      settle(api.batchReconcile(), null),
      settle(api.agentHealth(), null)
    ]).then(function (r) {
      return { health: r[0], reconcile: r[1], agent: r[2] };
    });
  }

  // 把探针结果翻译成三枚芯片。**全部字段来自接口真实返回**：
  //   health    → data.status / data.doris
  //   reconcile → data.latest.is_pass / .mismatched_windows / .batch_windows
  //               （**注意是 latest 里**，不是 data 顶层；见下方 ② 的说明）
  //   agent     → data.status / data.engine
  // 任何一项拿不到就显示"未知"，不猜、不编。
  function buildStatusChips(probe) {
    var chips = [];
    var p = probe || {};

    // ① 数据服务 + Doris
    // 注意：局部变量名**不能**叫 h —— 外层 h 是 Vue.h（创建 vnode 用），
    // 在内层作用域覆盖它会让同名函数在别处静默失效（本项目已踩过同类坑）。
    var health = p.health && p.health.ok ? (p.health.value || {}) : null;
    if (health && health.status === 'ok') {
      var doris = health.doris === 'ok' ? 'Doris 正常' : ('Doris ' + (health.doris || '未知'));
      var lat = (health.doris_latency_ms === undefined || health.doris_latency_ms === null)
        ? '' : ' · ' + health.doris_latency_ms + ' ms';
      chips.push({
        key: 'api', tone: health.doris === 'ok' ? 'ok' : 'warn',
        label: '数据服务', value: doris + lat,
        title: 'GET /data/api/health：' + doris +
          (health.readonly_user ? '；只读账号 ' + health.readonly_user : '')
      });
    } else {
      chips.push({
        key: 'api', tone: 'unknown', label: '数据服务', value: '未就绪',
        title: '健康检查未返回，或数据服务不可用'
      });
    }

    // ② 批流对账（本平台的差异化能力，所以在首页就要看得见）
    //
    // !! 字段位置：必须在 `latest` 里取，不是 data 顶层 !!
    //   实测 GET /batch/reconcile 的 data 是：
    //     { latest: {batch_id, batch_windows, matched_windows,
    //                mismatched_windows, is_pass, compared_at, scope_*},
    //       totals: {realtime:{...}, batch:{...}}, deltas:{...}, mismatches:[...] }
    //   我第一版写成直接在 data 上取 batch_windows —— 那个字段不存在，
    //   于是首页芯片恒显示"未取得"，而 Batch 页（读 .latest）正常。
    //   这类"取错层级"不会报错、只会静默显示兜底文案，所以只能靠对比实测发现。
    var rc = p.reconcile && p.reconcile.ok ? (p.reconcile.value || {}) : null;
    var rl = rc && rc.latest;
    if (rl && rl.batch_windows !== undefined && rl.batch_windows !== null) {
      var pass = rl.is_pass === true || rl.is_pass === 1 || rl.is_pass === '1';
      var mism = formatInt(rl.mismatched_windows);
      chips.push({
        key: 'reconcile', tone: pass ? 'ok' : 'bad',
        label: '批流对账', value: (pass ? 'PASS' : 'FAIL') + ' · 不一致 ' + mism,
        title: '对账批次 ' + (rl.batch_id || '—') +
          '；比对窗口 ' + formatInt(rl.batch_windows) +
          '；不一致 ' + mism +
          (rl.compared_at ? '；执行于 ' + formatTime(rl.compared_at) : '')
      });
    } else {
      chips.push({
        key: 'reconcile', tone: 'unknown', label: '批流对账', value: '未取得',
        title: '未取到对账结论（离线流水线可能尚未运行过对账阶段）'
      });
    }

    // ③ Agent
    var ag = p.agent && p.agent.ok ? (p.agent.value || {}) : null;
    if (ag && ag.status) {
      var ready = ag.status === 'ok';
      chips.push({
        key: 'agent', tone: ready ? 'ok' : 'warn',
        label: 'Agent', value: ready ? ('就绪 · ' + (ag.engine || 'ready')) : ag.status,
        title: 'GET /data/agent/health：' + ag.status +
          (ag.engine ? '；编排引擎 ' + ag.engine : '')
      });
    } else {
      chips.push({
        key: 'agent', tone: 'unknown', label: 'Agent', value: '未就绪',
        title: '问答 Agent 健康检查未返回'
      });
    }

    return chips;
  }

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
  //   - 主题（坐标轴 / 图例 / 提示框 / 无动画）来自 CHART_THEME，页面只传差异。
  function renderChart(el, option) {
    if (!el || !option || !window.echarts) return null;
    var instance = echarts.getInstanceByDom(el) || echarts.init(el, null, { renderer: 'canvas' });
    instance.clear();
    instance.setOption(merge(CHART_THEME, option), true);
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

  // 侧栏分组（Sprint 13 前端收口）
  //
  // !! 这里只是"把已有路由分组展示"，**不新增业务页** !!
  //   FRONTEND_UI_AUDIT §2 的原则：只重排/重命名现有路由。
  //   分组名表达平台的四层能力：概览 / 实时分析 / 离线与可信 / AI / 治理。
  //
  // 为什么 `monitor` 不在 ROUTES 里：
  //   它是 **Grafana 外链**，不是 SPA 路由。若混进 ROUTES，
  //   readHash() 会把它当成一个合法页面 key，而 #/monitor 下没有任何模板，
  //   结果是白屏。外链单独放在 NAV_GROUPS 里，走 <a target="_blank">。
  var MONITOR_URL = (function () {
    // 与 /data/ 同级部署：站点根 + /grafana/
    // 用 location 推导而不是硬编码 IP，换域名后不用改代码。
    var p = window.location.pathname.replace(/\/data\/.*$/, '/');
    return p + 'grafana/';
  })();

  var ROUTES = [
    { key: 'overview', title: '总览', subtitle: '核心指标、交易与流量趋势、类目 Top5' },
    { key: 'realtime', title: '实时链路', subtitle: '实时 ADS 分钟窗口：交易、流量与新鲜度' },
    { key: 'trade', title: '交易分析', subtitle: 'GMV、客单价、支付成功率与退款率' },
    { key: 'traffic', title: '流量分析', subtitle: 'UV / PV、行为漏斗与转化率' },
    { key: 'category', title: '类目销售', subtitle: '类目 GMV 排行、占比与明细' },
    { key: 'orders', title: '订单明细', subtitle: '按类目与日期筛选，查看订单支付与退款记录' },
    { key: 'batch', title: '离线与对账', subtitle: '离线分层指标、按天趋势，以及实时/离线逐窗口对账结论' },
    { key: 'ask', title: '数据问答', subtitle: '用自然语言提问，Agent 经只读接口取数并给出可核对的来源' },
    { key: 'quality', title: '数据质量', subtitle: '校验清单、覆盖类别与运行方式（CLI 驱动）' },
    { key: 'metrics', title: '指标口径', subtitle: '指标字典与表结构（Agent 回答问题的口径来源）' }
  ];

  // 导航分组：items 里引用路由 key，渲染时再去 ROUTES 取标题，
  // 避免同一份标题写两遍（写两遍必然漂移）。
  var NAV_GROUPS = [
    { title: '概览', keys: ['overview'] },
    { title: '实时分析', keys: ['realtime', 'trade', 'traffic', 'category', 'orders'] },
    { title: '离线与可信', keys: ['batch'] },
    { title: 'AI', keys: ['ask'] },
    { title: '治理', keys: ['quality', 'metrics'] }
  ];

  var currentPage = ref('overview');

  // 把 NAV_GROUPS 展开成渲染所需的形状：补标题、补外链、补标记。
  //
  // 只在这里做一次映射，模板里就能直接 v-for —— 模板里不做查找逻辑，
  // 是因为 x-template 没有类型检查，逻辑越少越不容易在改文案时写崩。
  var navGroups = computed(function () {
    var byKey = {};
    ROUTES.forEach(function (r) { byKey[r.key] = r; });

    var groups = NAV_GROUPS.map(function (g) {
      var items = [];
      g.keys.forEach(function (k) {
        var r = byKey[k];
        if (!r) return;  // 分组里写了不存在的 key：跳过而不是渲染一个死链
        items.push({ key: r.key, title: r.title, external: false, href: null, tag: null });
      });
      return { title: g.title, items: items };
    }).filter(function (g) { return g.items.length > 0; });

    // 系统监控：Grafana 外链，挂在"治理"组下（与"指标口径"同属治理能力）。
    // 它是**外链不是路由**，所以不放进 ROUTES（见 ROUTES 上方注释）。
    for (var i = 0; i < groups.length; i++) {
      if (groups[i].title === '治理') {
        groups[i].items.push({
          key: 'monitor',
          title: '系统监控',
          external: true,
          href: MONITOR_URL,
          tag: 'Grafana'
        });
      }
    }
    return groups;
  });

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
    props: {
      name: { type: String, default: '' },
      // 图标尺寸由调用方决定，避免满屏同一个 18px
      size: { type: [Number, String], default: 18 }
    }
  };

  var Skeleton = {
    name: 'Skeleton',
    template: '#tpl-skeleton',
    props: {
      rows: { type: Number, default: 4 },
      // 'kpi' 时按指标卡形状占位，避免加载完成时的布局跳动
      variant: { type: String, default: 'text' }
    },
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
      hint: { type: String, default: '' },
      // 图表位空态：给一个与图表等高（--chart-md）的容器，避免页面高度塌陷
      chart: { type: Boolean, default: false }
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
      tip: { type: String, default: '' },
      // 域语义色：trade / traffic / category，对应卡片顶部细线
      domain: { type: String, default: '' },
      // 一级主指标：字号提到 --fs-kpi-hero（28px），每页最多 1~2 个。
      // 它解决的是"6 张卡同字号、GMV 与退款笔数视觉等权"这个层级问题。
      hero: { type: Boolean, default: false },
      // 跨 2 列（配合 .kpi-grid 的列轨道使用）
      wide: { type: Boolean, default: false },
      // 环比：{ text, glyph, tone, pending, a11y, title }
      delta: { type: Object, default: null }
    },
    computed: {
      // 面板传入的要么是已格式化字符串，要么是原始值；null 一律显示 —
      displayValue: function () {
        if (isMissing(this.value)) return '—';
        return String(this.value);
      },
      valueClass: function () {
        return this.displayValue === '—' ? 'is-missing' : '';
      },
      // 颜色之外用箭头字符与 title 文案做冗余表达（不依赖颜色单独传达涨跌）
      deltaClass: function () {
        var tone = this.delta && this.delta.tone;
        return tone === 'up' ? 'is-up' : (tone === 'down' ? 'is-down' : 'is-flat');
      },
      deltaGlyph: function () {
        return (this.delta && this.delta.glyph) || '';
      },
      deltaTitle: function () {
        return (this.delta && this.delta.title) || '';
      }
    }
  };

  // 自绘下拉：原生 <select> 无法统一深色主题下的展开面板样式
  var SelectBox = {
    name: 'SelectBox',
    template: '#tpl-select',
    props: {
      modelValue: { default: '' },
      options: { type: Array, default: function () { return []; } },
      ariaLabel: { type: String, default: '下拉选择' }
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

      function toggle() { open.value = !open.value; }

      function onDocClick(event) {
        if (root.value && !root.value.contains(event.target)) open.value = false;
      }

      onMounted(function () { document.addEventListener('click', onDocClick); });
      onBeforeUnmount(function () { document.removeEventListener('click', onDocClick); });

      return { open: open, root: root, selectedLabel: selectedLabel, pick: pick, toggle: toggle };
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
      var statusProbe = ref(null);

      var tradeEl = ref(null);
      var trafficEl = ref(null);
      var categoryEl = ref(null);

      var categoryTop = computed(function () {
        var data = overview.value;
        return (data && data.category_top) || [];
      });

      function load() {
        return panel.run(function () {
          // 状态探针与主数据**并行**发起，且不参与 Promise.all 的失败传播：
          // probeStatus 内部每条已 catch，所以它自己不会 reject。
          var probe = probeStatus().then(function (r) { statusProbe.value = r; });
          return Promise.all([
            api.overview(60),
            api.trade(60),
            api.traffic(60),
            probe
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

      var statusChips = computed(function () { return buildStatusChips(statusProbe.value); });
      var monitorUrl = MONITOR_HREF;

      // —— 一级 Hero：GMV ——
      var heroGmv = computed(function () {
        var data = overview.value;
        if (!data) {
          return { label: 'GMV（下单金额）', value: null, unit: '元', sub: '', tip: definitionOf('gmv'), delta: null };
        }
        var kpi = data.kpi || {};
        var windows = data.windows || {};
        return {
          label: 'GMV（下单金额）',
          value: formatMoney(kpi.gmv),
          unit: '元',
          sub: '最近 ' + formatInt(windows.trade) + ' 个交易窗口（分钟级）累计',
          tip: definitionOf('gmv'),
          delta: buildDelta(trade.value, 'gmv', 'money', ' 元')
        };
      });

      // —— 一级 Hero：批流一致性 ——
      //   判据与状态芯片**同源**（都读 /batch/reconcile 的 is_pass），
      //   避免"卡片说 PASS、芯片说 FAIL"这种自相矛盾。
      var reconcileCard = computed(function () {
        var rc = statusProbe.value && statusProbe.value.reconcile;
        var payload = rc && rc.ok ? (rc.value || {}) : null;
        // 与状态芯片同一个坑：结论在 payload.latest 里，不是顶层
        var d = payload && payload.latest;
        if (!d || d.batch_windows === undefined || d.batch_windows === null) {
          return {
            label: '批流一致性（实时 vs 离线）', value: '—', unit: '',
            sub: '未取到对账结论', domain: '', tip: '逐窗口比对的结论来自 GET /batch/reconcile'
          };
        }
        var pass = d.is_pass === true || d.is_pass === 1 || d.is_pass === '1';
        return {
          label: '批流一致性（实时 vs 离线）',
          value: pass ? 'PASS' : 'FAIL',
          unit: '',
          sub: '比对窗口 ' + formatInt(d.batch_windows) + ' · 不一致 ' + formatInt(d.mismatched_windows),
          domain: pass ? '' : 'traffic',
          tip: '在两侧数据范围求交、尾部留 3 分钟安全边界的**对账区间内**，按 window_start 全外连接、' +
            '缺失侧补 0，逐窗口比对交易域 7 个可加指标 + 1 个去重指标；不一致窗口数为 0 即 PASS。' +
            '注意 matched 含"两侧均为 0"的业务静默窗口，属正常而非异常。'
        };
      });

      // —— 二级 KPI：4 卡 ——
      // 环比取"最近两个窗口"，两个窗口都非零时才展示（见 buildDelta 的说明）。
      var secondaryCards = computed(function () {
        var data = overview.value;
        if (!data) return [];
        var kpi = data.kpi || {};
        var latest = trade.value.length ? trade.value[trade.value.length - 1] : null;
        var windowText = data.latest_window ? '窗口 ' + shortTime(data.latest_window) : '';

        return [
          {
            label: '订单量', value: formatInt(kpi.order_cnt), unit: '笔', domain: 'trade',
            sub: '下单用户 ' + formatInt(kpi.order_user_cnt) + ' 人', tip: definitionOf('order_cnt'),
            delta: buildDelta(trade.value, 'order_cnt', 'int', ' 笔')
          },
          {
            label: '支付金额', value: formatMoney(kpi.payment_amount), unit: '元', domain: 'trade',
            sub: '支付笔数 ' + formatInt(kpi.payment_cnt) + ' 笔', tip: definitionOf('payment_amount'),
            delta: buildDelta(trade.value, 'payment_amount', 'money', ' 元')
          },
          {
            label: '退款金额', value: formatMoney(kpi.refund_amount), unit: '元', domain: 'trade',
            sub: '退款笔数 ' + formatInt(kpi.refund_cnt) + ' 笔', tip: definitionOf('refund_amount'),
            delta: buildDelta(trade.value, 'refund_amount', 'money', ' 元')
          },
          {
            label: 'PV（行为事件）', value: formatInt(kpi.pv), unit: '次', domain: 'traffic',
            sub: windowText || '行为事件总数', tip: definitionOf('pv'),
            delta: latest ? null : null
          }
        ];
      });

      // —— 交易趋势：GMV 折线（带渐隐面积） + 订单量柱 ——
      bindChart(function () { return tradeEl.value; }, function () {
        var rows = trade.value;
        if (!rows.length) return null;
        var labels = rows.map(function (r) { return shortTime(r.window_start); });
        return {
          color: [C.trade, C.neutral],
          tooltip: {
            axisPointer: { type: 'cross', label: { backgroundColor: '#1b2739', color: C.text2 }, crossStyle: { color: C.grid } }
          },
          legend: { data: ['GMV', '订单量'] },
          xAxis: timeAxis(labels),
          yAxis: [moneyAxis(), intAxis({ splitLine: { show: false } })],
          series: [
            makeLine('GMV', rows.map(function (r) { return numberOrNull(r.gmv); }), C.trade, 0),
            makeBar('订单量', rows.map(function (r) { return numberOrNull(r.order_cnt); }), withAlpha(C.neutral, 0.75), { yAxisIndex: 1 })
          ]
        };
      });

      // —— 流量趋势：PV / UV 折线 ——
      bindChart(function () { return trafficEl.value; }, function () {
        var rows = traffic.value;
        if (!rows.length) return null;
        var labels = rows.map(function (r) { return shortTime(r.window_start); });
        return {
          color: [C.traffic, C.warn],
          legend: { data: ['PV', 'UV'] },
          xAxis: timeAxis(labels),
          yAxis: [intAxis()],
          series: [
            makeLine('PV', rows.map(function (r) { return numberOrNull(r.pv); }), C.traffic, 0),
            makeLine('UV', rows.map(function (r) { return numberOrNull(r.uv); }), C.warn, 0)
          ]
        };
      });

      // —— 类目 Top5：横向条形（GMV 降序，ECharts 类目轴自下而上，故需反转） ——
      bindChart(function () { return categoryEl.value; }, function () {
        var rows = categoryTop.value.slice(0, 5);
        if (!rows.length) return null;
        var ordered = rows.slice().reverse();
        return {
          color: [C.category],
          tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(148,163,184,0.08)' } },
            valueFormatter: function (v) { return formatMoney(v) + ' 元'; }
          },
          grid: { left: 6, right: 104, top: 14, bottom: 4, containLabel: true },
          xAxis: moneyAxis({ splitLine: { show: false }, axisLabel: { show: false } }),
          yAxis: rankAxis(ordered.map(function (r) { return r.category_name; })),
          series: [makeRankBar('GMV', ordered.map(function (r) { return numberOrNull(r.gmv); }), C.category)]
        };
      });

      return {
        loading: panel.loading,
        error: panel.error,
        overview: overview,
        trade: trade,
        traffic: traffic,
        categoryTop: categoryTop,
        statusChips: statusChips,
        heroGmv: heroGmv,
        reconcileCard: reconcileCard,
        secondaryCards: secondaryCards,
        monitorUrl: monitorUrl,
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
      var pageSize = PAGE_SIZE;

      function load() {
        return panel.run(function () {
          return api.trade(TRADE_WINDOW_LIMIT).then(function (rows) {
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
        return Math.max(1, Math.ceil(rowsDesc.value.length / pageSize));
      });
      var pagedRows = computed(function () {
        var start = (page.value - 1) * pageSize;
        return rowsDesc.value.slice(start, start + pageSize);
      });

      // 页码按钮：首尾恒显 + 当前页 ±1，中间用省略号（页数多时不做无意义的 200 个按钮）
      var pageList = computed(function () {
        var total = pageCount.value;
        var cur = page.value;
        var items = [];
        var push = function (p) {
          items.push({ key: 'p' + p, page: p, label: String(p), current: p === cur, gap: false });
        };
        var pages = [];
        if (total <= 7) {
          for (var i = 1; i <= total; i++) pages.push(i);
        } else {
          pages.push(1);
          for (var j = cur - 1; j <= cur + 1; j++) { if (j > 1 && j < total) pages.push(j); }
          pages.push(total);
        }
        var prev = 0;
        pages.forEach(function (p) {
          if (prev && p - prev > 1) {
            items.push({ key: 'gap' + p, page: prev, label: '…', current: false, gap: true });
          }
          push(p);
          prev = p;
        });
        return items;
      });

      function goPage(target) {
        var next = clamp(target, 1, pageCount.value);
        if (next === page.value) return;
        page.value = next;
      }

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

      // —— 交易指标卡：一级 2 张（GMV / 客单价）+ 二级 2 张 ——
      //
      // 为什么拆成三段而不是一个数组：
      //   模板里一级与二级用**不同的栅格**（hero 2 列 / 二级 4 列），
      //   一个数组没法同时喂给两个 grid；拆开比在模板里 slice 更好读。
      // 客单价提到一级的理由：它是"每单多少钱"，与 GMV 合计构成
      //   "总量 × 单价"这一对最基本的经营读数；支付成功率与退款率
      //   是质量类指标，属于二级。
      var cardModel = computed(function () {
        var t = totals.value;
        var avgCents = (t.gmvCents !== null && t.orders > 0) ? (t.gmvCents / BigInt(t.orders)) : null;
        var successRate = (t.payCnt + t.payFail) > 0 ? (t.payCnt / (t.payCnt + t.payFail)) : null;
        // 退款率同样避开大额浮点：先在"整数分"上算出万分比，再换算成小数
        var refundRate = (t.refundCents !== null && t.payCents !== null && t.payCents > 0n)
          ? Number((t.refundCents * 10000n) / t.payCents) / 10000 : null;
        var span = series.value.length
          ? shortTime(series.value[0].window_start) + ' ~ ' + shortTime(series.value[series.value.length - 1].window_start)
          : '暂无窗口';

        return {
          hero: {
            label: 'GMV 合计', value: formatCents(t.gmvCents), unit: '元', domain: 'trade',
            sub: span, tip: definitionOf('gmv'),
            delta: buildDelta(series.value, 'gmv', 'money', ' 元')
          },
          avg: {
            label: '客单价', value: formatCents(avgCents), unit: '元', domain: 'trade',
            sub: 'GMV ÷ 订单量 ' + formatInt(t.orders) + ' 笔', tip: definitionOf('avg_order_amount'),
            delta: buildDelta(series.value, 'avg_order_amount', 'money', ' 元')
          },
          rest: [
            {
              label: '支付成功率', value: formatRate(successRate), unit: '', domain: 'trade',
              sub: '成功 ' + formatInt(t.payCnt) + ' / 失败 ' + formatInt(t.payFail), tip: definitionOf('payment_success_rate')
            },
            {
              label: '退款率（金额口径）', value: formatRate(refundRate), unit: '', domain: 'trade',
              sub: '退款 ' + formatCents(t.refundCents) + ' / 支付 ' + formatCents(t.payCents), tip: definitionOf('refund_rate')
            }
          ]
        };
      });

      var heroCard = computed(function () { return cardModel.value.hero; });
      var avgCard = computed(function () { return cardModel.value.avg; });
      var restCards = computed(function () { return cardModel.value.rest; });
      // 兼容：模板若仍引用 cards，给出同一批数据（避免遗漏引用时报 undefined）
      var cards = computed(function () {
        var m = cardModel.value;
        return [m.hero, m.avg].concat(m.rest);
      });

      // —— 双轴折线：GMV（左轴）+ 支付金额（右轴） ——
      bindChart(function () { return chartEl.value; }, function () {
        var rows = series.value;
        if (!rows.length) return null;
        var labels = rows.map(function (r) { return shortTime(r.window_start); });
        return {
          color: [C.trade, C.pos],
          tooltip: {
            valueFormatter: function (v) { return formatMoney(v) + ' 元'; }
          },
          legend: { data: ['GMV', '支付金额'] },
          xAxis: timeAxis(labels),
          yAxis: [moneyAxis(), moneyAxis({ splitLine: { show: false } })],
          series: [
            makeLine('GMV', rows.map(function (r) { return numberOrNull(r.gmv); }), C.trade, 0),
            makeLine('支付金额', rows.map(function (r) { return numberOrNull(r.payment_amount); }), C.pos, 1)
          ]
        };
      });

      return {
        loading: panel.loading,
        error: panel.error,
        series: series,
        chartEl: chartEl,
        cards: cards,
        heroCard: heroCard,
        avgCard: avgCard,
        restCards: restCards,
        page: page,
        pageCount: pageCount,
        pageSize: pageSize,
        pageList: pageList,
        goPage: goPage,
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
          { label: 'UV（单窗口峰值）', value: formatInt(s.uv), unit: '人', domain: 'traffic', sub: '去重用户不可跨窗口相加', tip: definitionOf('uv') },
          { label: 'PV 合计', value: formatInt(s.pv), unit: '次', domain: 'traffic', sub: '全部行为事件数', tip: definitionOf('pv') },
          { label: '浏览次数', value: formatInt(f.view_cnt), unit: '次', domain: 'traffic', sub: '漏斗第一层', tip: definitionOf('view_cnt') },
          { label: '购买次数', value: formatInt(f.buy_cnt), unit: '次', domain: 'traffic', sub: '收藏 ' + formatInt(s.favorite) + ' 次', tip: definitionOf('buy_cnt') }
        ];
      });

      var rateCards = computed(function () {
        var f = funnelTotal.value;
        return [
          { label: '点击率', value: formatRate(ratioOf(f.click_cnt, f.view_cnt)), unit: '', domain: 'traffic', sub: '点击 ÷ 浏览', tip: definitionOf('click_rate') },
          { label: '加购率', value: formatRate(ratioOf(f.cart_cnt, f.click_cnt)), unit: '', domain: 'traffic', sub: '加购 ÷ 点击', tip: definitionOf('cart_rate') },
          { label: '购买转化率', value: formatRate(ratioOf(f.buy_cnt, f.cart_cnt)), unit: '', domain: 'traffic', sub: '购买 ÷ 加购', tip: definitionOf('buy_rate') },
          { label: '整体转化率', value: formatRate(ratioOf(f.buy_cnt, f.view_cnt)), unit: '', domain: 'traffic', sub: '购买 ÷ 浏览（端到端）', tip: '端到端转化率 = buy_cnt / view_cnt，用于衡量整条链路的漏损。' }
        ];
      });

      var steps = computed(function () {
        var f = funnelTotal.value;
        var pairs = [
          { label: '浏览 → 点击', from: '浏览', to: '点击', a: f.view_cnt, b: f.click_cnt },
          { label: '点击 → 加购', from: '点击', to: '加购', a: f.click_cnt, b: f.cart_cnt },
          { label: '加购 → 购买', from: '加购', to: '购买', a: f.cart_cnt, b: f.buy_cnt }
        ];
        return pairs.map(function (p) {
          return {
            label: p.label,
            value: formatRate(ratioOf(p.b, p.a)),
            note: '流失 ' + formatInt(p.a - p.b),
            // 断点判据：**这一级的分母为 0**（而不是"结果为 0"）。
            // 分母为 0 时该级及其之后的比率在数学上都无意义，
            // 与"比例恰好算出来是 0%"是两回事，不能混为一谈。
            zero: !p.a
          };
        });
      });

      // 漏斗断点：找出**最靠前**的那个"分母为 0"的级。
      // 为什么只报第一个：后面的必然也为 0，全部报出来只是噪声；
      // 修的人只需要知道"从哪一级开始断的"。
      var funnelBreak = computed(function () {
        var f = funnelTotal.value;
        var chain = [
          { name: '浏览', v: f.view_cnt },
          { name: '点击', v: f.click_cnt },
          { name: '加购', v: f.cart_cnt },
          { name: '购买', v: f.buy_cnt }
        ];
        for (var i = 0; i < chain.length - 1; i++) {
          if (!chain[i].v) {
            return { from: chain[i].name, to: chain[chain.length - 1].name };
          }
        }
        return null;
      });

      // —— 漏斗图：ECharts funnel，标签显示层名与累计值 ——
      bindChart(function () { return funnelEl.value; }, function () {
        var f = funnelTotal.value;
        if (!f.view_cnt) return null;
        var funnelColors = [C.traffic, withAlpha(C.traffic, 0.78), withAlpha(C.traffic, 0.58), C.trade];
        return {
          color: funnelColors,
          // 漏斗没有坐标轴，图例与网格按需关掉，标签直接落在色块上
          legend: { show: false },
          grid: { left: 0, right: 0, top: 0, bottom: 0, containLabel: false },
          tooltip: {
            trigger: 'item',
            formatter: function (p) {
              var total = funnelTotal.value.view_cnt;
              var share = total ? ((p.value / total) * 100).toFixed(2) + '%' : '—';
              return p.name + '<br/>数量：' + formatInt(p.value) + '<br/>占浏览：' + share;
            }
          },
          series: [{
            type: 'funnel',
            left: '5%',
            right: '5%',
            top: 16,
            bottom: 16,
            minSize: '26%',
            sort: 'descending',
            gap: 3,
            label: { color: C.text2, fontSize: 12, formatter: '{b}  {c}' },
            labelLine: { length: 10, lineStyle: { color: C.grid } },
            itemStyle: { borderColor: 'transparent', borderWidth: 0, opacity: 0.9 },
            emphasis: { label: { color: C.text, fontWeight: 'bold' } },
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
          color: [C.traffic, C.warn, C.neutral],
          legend: { data: ['PV', 'UV', '浏览次数'] },
          xAxis: timeAxis(labels),
          yAxis: [intAxis()],
          series: [
            makeLine('PV', rows.map(function (r) { return numberOrNull(r.pv); }), C.traffic, 0),
            makeLine('UV', rows.map(function (r) { return numberOrNull(r.uv); }), C.warn, 0),
            makeLine('浏览次数', rows.map(function (r) { return numberOrNull(r.view_cnt); }), C.neutral, 0)
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
        funnelBreak: funnelBreak,
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
          color: [C.category],
          tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(148,163,184,0.08)' } },
            formatter: function (params) {
              var p = Array.isArray(params) ? params[0] : params;
              var row = ordered[p.dataIndex] || {};
              return row.category_name + '<br/>GMV：' + formatMoney(row.gmv) + ' 元' +
                '<br/>订单量：' + formatInt(row.order_cnt) + ' 笔' +
                '<br/>客单价：' + formatMoney(row.avg_order_amount) + ' 元';
            }
          },
          grid: { left: 6, right: 104, top: 14, bottom: 4, containLabel: true },
          xAxis: moneyAxis({ splitLine: { show: false }, axisLabel: { show: false } }),
          yAxis: rankAxis(ordered.map(function (r) { return r.category_name; })),
          series: [makeRankBar('GMV', ordered.map(function (r) { return numberOrNull(r.gmv); }), C.category)]
        };
      });

      // —— 占比饼图：用环形展示，避免大屏正中大面积实色 ——
      bindChart(function () { return pieEl.value; }, function () {
        var items = list.value;
        if (!items.length) return null;
        return {
          color: C.series,
          legend: {
            bottom: 0,
            left: 'center',
            icon: 'circle',
            itemWidth: 8,
            itemHeight: 8,
            itemGap: 12,
            textStyle: { color: C.axis, fontSize: 11 }
          },
          tooltip: {
            trigger: 'item',
            formatter: function (p) {
              return p.name + '<br/>GMV：' + formatMoney(p.value) + ' 元<br/>占比：' + p.percent + '%';
            }
          },
          series: [{
            type: 'pie',
            radius: ['46%', '70%'],
            center: ['50%', '45%'],
            avoidLabelOverlap: true,
            // 用面板底色描边形成缝隙，比白色描边更贴合深色主题
            itemStyle: { borderColor: C.surface, borderWidth: 2 },
            label: { color: C.text2, fontSize: 11, formatter: '{b} {d}%' },
            labelLine: { length: 8, length2: 8, lineStyle: { color: C.grid } },
            emphasis: { scale: true, scaleSize: 4, label: { color: C.text } },
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
      var modalEl = ref(null);

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

      // 自绘下拉需要 { label, value } 结构；空值用 '' 表示"全部类目"
      var categorySelectOptions = computed(function () {
        var opts = [{ label: '全部类目', value: '' }];
        categoryOptions.value.forEach(function (name) {
          opts.push({ label: name, value: name });
        });
        return opts;
      });

      var dateRangeInvalid = computed(function () {
        return !!(startDate.value && endDate.value && startDate.value > endDate.value);
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

      // 弹窗打开时把焦点移入面板（键盘与读屏用户不会"丢失焦点"），关闭时归还给触发它的行
      var lastFocused = null;

      function openDetail(orderId) {
        if (detailLoading.value) return;
        if (!detailOpen.value) lastFocused = document.activeElement;
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
          Vue.nextTick(function () {
            if (modalEl.value) modalEl.value.focus();
          });
        });
      }

      function closeDetail() {
        detailOpen.value = false;
        detail.value = null;
        detailError.value = '';
        Vue.nextTick(function () {
          if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
          lastFocused = null;
        });
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
        categorySelectOptions: categorySelectOptions,
        dateRangeInvalid: dateRangeInvalid,
        filterSummary: filterSummary,
        page: page,
        pageCount: pageCount,
        offset: offset,
        limit: PAGE_SIZE,
        modalEl: modalEl,
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
  // ---------- 页面 7：离线与对账（Sprint 3） ----------
  // ============================================================
  //
  // 这一页回答的问题与其它页不同：
  //   其它页回答"业务上发生了什么"；这一页回答"**这个数可不可信**"。
  //   它同时展示离线链路算出的指标，以及实时/离线两条链路的逐窗口对账结论。
  //   对账是离线 Spark 作业完成的，这一页只读结论（服务层不参与对账）。

  // 日粒度的 X 轴：日期只需 MM-DD，避免 60 天的标签互相压字
  function shortDate(v) {
    if (isMissing(v)) return '';
    var s = String(v);
    return s.length >= 10 ? s.slice(5, 10) : s;
  }

  var BatchPanel = {
    name: 'BatchPanel',
    template: '#tpl-batch',
    setup: function () {
      var panel = usePanel();
      var overview = ref(null);
      var reconcile = ref(null);
      var days = ref(60);
      var dailyEl = ref(null);
      var categoryEl = ref(null);

      var dayOptions = [
        { value: 30, label: '最近 30 天' },
        { value: 60, label: '最近 60 天' },
        { value: 180, label: '最近 180 天' },
        { value: 365, label: '最近 365 天' }
      ];

      function load() {
        return panel.run(function () {
          return Promise.all([
            api.batchOverview(days.value),
            api.batchReconcile()
          ]).then(function (res) {
            overview.value = res[0] || null;
            reconcile.value = res[1] || null;
            return res[0] || null;
          });
        }, function () { return null; });
      }
      panel.setReload(load);
      onMounted(load);

      function changeDays(value) {
        var next = Number(value && value.value !== undefined ? value.value : value);
        if (!next || next === days.value) return;
        days.value = next;
        load();
      }

      var daily = computed(function () {
        var data = overview.value;
        return (data && data.daily) || [];
      });
      var categoryTop = computed(function () {
        var data = overview.value;
        return (data && data.category_top) || [];
      });
      var isPass = computed(function () {
        var latest = reconcile.value && reconcile.value.latest;
        return !!(latest && latest.is_pass);
      });
      var mismatches = computed(function () {
        return (reconcile.value && reconcile.value.mismatches) || [];
      });

      var kpiCards = computed(function () {
        var data = overview.value;
        if (!data) return [];
        var kpi = data.kpi || {};
        var range = data.time_range || {};
        var span = range.start && range.end
          ? shortDate(range.start) + ' ~ ' + shortDate(range.end)
          : '全量';
        return [
          {
            label: 'GMV（离线口径）', value: formatMoney(kpi.gmv), unit: '元', domain: 'trade',
            sub: '离线链路 · ' + span, tip: definitionOf('gmv')
          },
          {
            label: '订单量', value: formatInt(kpi.order_cnt), unit: '笔', domain: 'trade',
            sub: '按天聚合后求和', tip: definitionOf('order_cnt')
          },
          {
            label: '支付成功率', value: formatRate(kpi.payment_success_rate), unit: '', domain: 'trade',
            sub: '支付 ' + formatInt(kpi.payment_cnt) + ' 笔 / 失败 ' + formatInt(kpi.payment_fail_cnt) + ' 笔',
            tip: definitionOf('payment_success_rate')
          },
          {
            label: '退款金额', value: formatMoney(kpi.refund_amount), unit: '元', domain: 'trade',
            sub: '退款 ' + formatInt(kpi.refund_cnt) + ' 笔', tip: definitionOf('refund_amount')
          },
          {
            label: '退款率', value: formatRate(kpi.refund_rate), unit: '', domain: 'trade',
            sub: '退款金额 / 支付金额', tip: definitionOf('refund_rate')
          },
          {
            label: '客单价', value: formatMoney(kpi.avg_order_amount), unit: '元', domain: 'trade',
            sub: 'GMV / 订单量', tip: definitionOf('avg_order_amount')
          }
        ];
      });

      // 实时 vs 离线的并排对比：差异列直接展示，一眼看到"是不是同一个数"
      var compareRows = computed(function () {
        var rep = reconcile.value;
        if (!rep || !rep.totals) return [];
        var rt = rep.totals.realtime || {};
        var bt = rep.totals.batch || {};
        var deltas = rep.deltas || {};
        if (isMissing(rt.gmv) && isMissing(bt.gmv)) return [];
        return [
          {
            label: 'GMV（元）',
            realtime: formatMoney(rt.gmv), batch: formatMoney(bt.gmv),
            diff: formatDelta(deltas.gmv)
          },
          {
            label: '订单量（笔）',
            realtime: formatInt(rt.order_cnt), batch: formatInt(bt.order_cnt),
            diff: formatDelta(deltas.order_cnt)
          },
          {
            label: '支付金额（元）',
            realtime: formatMoney(rt.payment_amount), batch: formatMoney(bt.payment_amount),
            diff: formatDelta(deltas.payment_amount)
          },
          {
            label: '退款金额（元）',
            realtime: formatMoney(rt.refund_amount), batch: formatMoney(bt.refund_amount),
            diff: formatDelta(deltas.refund_amount)
          }
        ];
      });

      var compareAllZero = computed(function () {
        var rows = compareRows.value;
        if (!rows.length) return false;
        // 判据是"每行 diff 归零"，与表格里 diff-ok / diff-bad 的判据保持一致 ——
        // 两处若各写一套，会出现"表格全绿但标题说'有差异'"的自相矛盾。
        return rows.every(function (r) {
          return r.diff === '0' || r.diff === '0.00';
        });
      });

      bindChart(function () { return dailyEl.value; }, function () {
        var rows = daily.value;
        if (!rows.length) return null;
        var labels = rows.map(function (r) { return shortDate(r.dt); });
        return {
          color: [C.trade, C.neutral],
          tooltip: {
            axisPointer: { type: 'cross', label: { backgroundColor: '#1b2739', color: C.text2, crossStyle: { color: C.grid } } }
          },
          legend: { data: ['GMV', '订单量'] },
          xAxis: timeAxis(labels),
          yAxis: [moneyAxis(), intAxis({ splitLine: { show: false } })],
          series: [
            makeLine('GMV', rows.map(function (r) { return numberOrNull(r.gmv); }), C.trade, 0),
            makeBar('订单量', rows.map(function (r) { return numberOrNull(r.order_cnt); }),
              withAlpha(C.neutral, 0.75), { yAxisIndex: 1 })
          ]
        };
      });

      bindChart(function () { return categoryEl.value; }, function () {
        var rows = categoryTop.value;
        if (!rows.length) return null;
        var ordered = rows.slice().sort(function (a, b) {
          return Number(a.gmv || 0) - Number(b.gmv || 0);
        });
        return {
          color: [C.trade],
          grid: { left: 8, right: 24, top: 16, bottom: 8, containLabel: true },
          tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
          xAxis: intAxis({ axisLabel: { formatter: function (v) { return compactMoney(v); } } }),
          yAxis: {
            type: 'category',
            data: ordered.map(function (r) { return r.category_name; }),
            axisLabel: { color: C.text2 }
          },
          series: [makeBar('GMV', ordered.map(function (r) { return numberOrNull(r.gmv); }), C.trade)]
        };
      });

      return {
        loading: panel.loading,
        error: panel.error,
        days: days,
        dayOptions: dayOptions,
        changeDays: changeDays,
        daily: daily,
        categoryTop: categoryTop,
        kpiCards: kpiCards,
        reconcile: reconcile,
        isPass: isPass,
        compareRows: compareRows,
        compareAllZero: compareAllZero,
        mismatches: mismatches,
        dailyEl: dailyEl,
        categoryEl: categoryEl,
        shortDate: shortDate,
        shortTime: shortTime,
        formatInt: formatInt,
        formatMoney: formatMoney,
        formatTime: formatTime
      };
    }
  };

  // ============================================================
  // ---------- 页面 8：数据问答（Sprint 7） ----------
  // ============================================================
  //
  // 这一页的 UI 重点不是"聊天框好不好看"，而是**证据要看得见**：
  //   回答下面必须能展开"用了哪些表 / 实际执行的 SQL / 每一步调了什么工具"。
  //   因为 AGENTS.md 第 10.1 节要求「Agent 不得伪造查询结果」——
  //   光靠一句"我查了数据库"是无法验证的，得把可核对的东西摆出来。

  // 工具参数展示：把 JSON 压成一行，太长时截断（步骤只是给人扫一眼，不是给人读 SQL）
  function prettyArgs(args) {
    if (!args || typeof args !== 'object') return '（无参数）';
    var text;
    try {
      text = JSON.stringify(args, null, 2);
    } catch (e) {
      text = String(args);
    }
    return text.length > 600 ? text.slice(0, 600) + '\n…（已截断）' : text;
  }

  function formatMs(ms) {
    var n = numberOrNull(ms);
    if (n === null) return '—';
    return n < 1000 ? (n + ' ms') : ((n / 1000).toFixed(1) + ' s');
  }

  // ============================================================
  // ---------- 数据质量校验清单（前端静态镜像） ----------
  // ============================================================
  //
  // !! 这份清单是 infrastructure/quality/checks.conf 的**只读镜像** !!
  //
  // 为什么要在前端放一份，而不是从接口取：
  //   校验清单由 CLI 引擎（infrastructure/quality/run-check.sh）执行，
  //   数据服务**没有**质量校验的只读接口 —— 本次改造的硬约束是
  //   "不新增后端功能、不修改 API contract"，所以不能为这一页新开接口。
  //
  // 为什么不"顺手造"每个校验的 PASS/FAIL：
  //   FRONTEND_UI_AUDIT §10 与 IMPLEMENTATION_PROMPT 硬约束 6 都明确禁止
  //   "新建不存在后端的 Quality 完整页"与伪造数据。
  //   因此本页只展示两样**真实**的东西：
  //     ① 校验清单本身（标题/域/级别/执行引擎）—— 静态事实，来自 checks.conf；
  //     ② **真实可取的完整性信号** —— 来自已有的 GET /batch/reconcile
  //        （批流逐窗口对账）与 GET /meta/tables（数据服务实际放行的表白名单）。
  //   每一处都不写"通过/失败"，因为前端拿不到那个结论。
  //
  // 与 checks.conf 的一致性如何保证：
  //   tests/test_frontend_quality_manifest.py 会解析 checks.conf 并逐项比对
  //   本清单的 id/title，不一致就让测试变红 —— 而不是靠"记得同步"。
  var QUALITY_GROUPS = [
    {
      domain: 'trade',
      label: '交易域',
      desc: '订单 / 支付 / 退款三类事实的正确性',
      checks: [
        { id: 'rowcount_trade', title: '交易域非空守卫（实时 DWD 三表 + 离线 ADS）' },
        { id: 'pk_unique_trade', title: '交易域主键唯一（order_id / payment_id / refund_id）' },
        { id: 'notnull_trade', title: '交易域关键字段非空（主键 / user_id / amount / event_time）' },
        { id: 'amount_gmv_realtime', title: '金额关系：实时 DWD SUM(amount) == 离线 ADS SUM(gmv)' },
        { id: 'amount_payment_le_order', title: '金额关系：支付金额 <= 订单金额（不允许超付）' },
        { id: 'amount_refund_le_payment', title: '金额关系：退款金额 <= 支付金额（不允许超退）' },
        { id: 'enum_payment_status', title: '支付状态枚举合法（SUCCESS/FAILED）' },
        { id: 'fresh_ads_trade_1m', title: '新鲜度：ads_batch_trade_1m 最新窗口滞后 <= 48h' }
      ]
    },
    {
      domain: 'traffic',
      label: '流量域',
      desc: '行为事件与漏斗口径',
      checks: [
        { id: 'rowcount_traffic', title: '流量域非空守卫（行为明细 + 流量 ADS 三表）' },
        { id: 'pk_unique_traffic', title: '流量域主键唯一（event_id）' },
        { id: 'notnull_traffic', title: '流量域关键字段非空（user_id / event_time / event_type / dt）' },
        { id: 'enum_event_type', title: '行为枚举合法（VIEW/CLICK/CART/FAVORITE/BUY）' },
        { id: 'funnel_monotonic', title: '漏斗逐级收窄（VIEW>CLICK>CART>BUY）' },
        { id: 'fresh_ads_traffic_1m', title: '新鲜度：ads_traffic_1m 最新窗口滞后 <= 48h' },
        { id: 'fresh_ads_traffic_1d', title: '新鲜度：ads_traffic_1d 最新分区日期滞后 <= 48h' },
        { id: 'fresh_dwd_behavior_dt', title: '新鲜度：DWD 行为明细最新 dt 滞后 <= 48h' }
      ]
    },
    {
      domain: 'layer',
      label: '分层与存储',
      desc: '层间行数一致性与窗口键完整性（含 1 项需读 Iceberg）',
      checks: [
        { id: 'rowcount_layer', title: '分层表非空守卫（ODS/DWD/DWS/ADS 各层代表表）' },
        { id: 'ads_window_positive', title: 'ADS 窗口数 > 0（四个窗口粒度表）' },
        { id: 'pk_unique_ads', title: '离线 ADS 窗口主键唯一（trade 1m / traffic 1m + 1d）' },
        { id: 'notnull_ads_win', title: '离线 ADS 窗口字段非空（window_start / window_end）' },
        { id: 'ads_1m_1d_consistency', title: '离线 ADS 1m 汇总 == 1d 汇总（交易 2 项 + 流量 2 项）' },
        { id: 'dwd_ads_trade_parity', title: '层间一致：DWD 订单数 == 离线 ADS 订单数合计' },
        { id: 'dwd_ads_behavior_parity', title: '层间一致：DWD 行为明细 == ADS 窗口 PV 合计（20000）' },
        { id: 'dwd_ods_parity_all', title: '层间一致：DWD == ODS 逐表（湖仓 Iceberg，5 张交易表）', engine: 'spark' }
      ]
    },
    {
      domain: 'cross',
      label: '跨链路',
      desc: '实时链路与离线链路的一致性',
      checks: [
        { id: 'dwd_ads_order_parity', title: '层间一致：实时 DWD 订单数 == 离线 ADS 订单数' }
      ]
    }
  ];

  // ============================================================
  // ---------- 安全 Markdown 渲染（Ask 页回答正文） ----------
  // ============================================================
  //
  // !! 为什么不能用 v-html !!
  //   Agent 的回答是**模型生成的文本**，其中可能包含任意字符（含 <script>）。
  //   用 innerHTML / v-html 渲染等于把"模型输出"当成"可信 HTML"执行 ——
  //   一旦模型被诱导输出标签，就是一次 XSS。
  //   所以这里**只生成 vnode**（Vue 的 h()），全程不碰 innerHTML：
  //   Vue 会把文本节点当纯文本处理，标签天然不会被解析成元素。
  //
  // 支持范围（刻意很小，够用即可）：
  //   ## / ###  标题        - / * 无序列表        1. 有序列表
  //   | a | b | 表格        **粗体**               `行内代码`
  //   ``` 代码块            水平线 ---
  // 不支持也不需要：链接、图片、原生 HTML（回答里出现就按纯文本显示）。
  //
  // 为什么自己写而不引 marked/markdown-it：
  //   项目硬约束"禁止引入 npm 依赖"，而这两者都是 npm 包。
  function mdInline(text) {
    // 行内解析：**粗体** 与 `代码`。用一次扫描完成，避免正则互相吃掉。
    var out = [];
    var re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    var last = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) out.push(text.slice(last, m.index));
      var tok = m[0];
      if (tok.slice(0, 2) === '**') {
        out.push(h('strong', null, tok.slice(2, -2)));
      } else {
        out.push(h('code', { class: 'md-code' }, tok.slice(1, -1)));
      }
      last = m.index + tok.length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out.length ? out : [text];
  }

  function mdRow(line) {
    // "| a | b |" → ['a','b']；去掉首尾空单元格
    var cells = line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|');
    return cells.map(function (c) { return c.trim(); });
  }

  function mdIsDivider(line) {
    // 表格分隔行：| --- | :--: |
    return /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.indexOf('-') >= 0;
  }

  function renderMarkdown(src) {
    var text = String(src === undefined || src === null ? '' : src);
    if (!text.trim()) return [];

    var lines = text.replace(/\r\n?/g, '\n').split('\n');
    var blocks = [];
    var i = 0;

    while (i < lines.length) {
      var line = lines[i];

      // 代码块
      if (/^\s*```/.test(line)) {
        var buf = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; // 跳过结束的 ```
        blocks.push(h('pre', { class: 'md-pre' }, h('code', null, buf.join('\n'))));
        continue;
      }

      // 标题
      var hm = /^(#{1,6})\s+(.*)$/.exec(line);
      if (hm) {
        var level = Math.min(hm[1].length + 2, 6); // h1→h3：页面里 h1/h2 已被顶栏与面板占用
        blocks.push(h('h' + level, { class: 'md-h' }, mdInline(hm[2])));
        i++;
        continue;
      }

      // 水平线
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
        blocks.push(h('hr', { class: 'md-hr' }));
        i++;
        continue;
      }

      // 表格：当前行是 |...| 且下一行是分隔行
      if (/^\s*\|/.test(line) && i + 1 < lines.length && mdIsDivider(lines[i + 1])) {
        var head = mdRow(line);
        i += 2;
        var body = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) { body.push(mdRow(lines[i])); i++; }
        var thead = h('thead', null, h('tr', null, head.map(function (c) {
          return h('th', { scope: 'col' }, mdInline(c));
        })));
        var tbody = h('tbody', null, body.map(function (r) {
          return h('tr', null, head.map(function (_, ci) {
            return h('td', null, mdInline(r[ci] === undefined ? '' : r[ci]));
          }));
        }));
        blocks.push(h('div', { class: 'md-table-wrap' }, h('table', { class: 'table table--compact md-table' }, [thead, tbody])));
        continue;
      }

      // 列表（有序 / 无序）：连续行归为同一个列表
      var lm = /^\s*(?:([-*+])\s+(.*)|(\d+)\.\s+(.*))$/.exec(line);
      if (lm) {
        var ordered = lm[3] !== undefined;
        var items = [];
        while (i < lines.length) {
          var m2 = /^\s*(?:([-*+])\s+(.*)|(\d+)\.\s+(.*))$/.exec(lines[i]);
          if (!m2 || (m2[3] !== undefined) !== ordered) break;
          items.push(h('li', null, mdInline(m2[3] !== undefined ? m2[4] : m2[2])));
          i++;
        }
        blocks.push(h(ordered ? 'ol' : 'ul', { class: 'md-list' }, items));
        continue;
      }

      // 空行
      if (!line.trim()) { i++; continue; }

      // 段落：把连续的普通行合成一段（Markdown 的软换行按空格接续）
      var para = [];
      while (i < lines.length && lines[i].trim()
             && !/^\s*```/.test(lines[i]) && !/^#{1,6}\s/.test(lines[i])
             && !/^\s*\|/.test(lines[i]) && !/^\s*(?:[-*+]\s|\d+\.\s)/.test(lines[i])
             && !/^\s*(-{3,}|\*{3,})\s*$/.test(lines[i])) {
        para.push(lines[i].trim());
        i++;
      }
      blocks.push(h('p', { class: 'md-p' }, mdInline(para.join(' '))));
    }
    return blocks;
  }

  // 执行轨：把**服务端返回的 graph.path / steps / executed_sql** 映射成固定 6 段。
  //
  // !! 这不是 chain-of-thought !!
  //   每一段都对应"服务端确实报告过的动作"：
  //     Query      —— 用户提问（永远发生）
  //     Retrieval  —— graph.path 含 'retrieve'，或 docs 非空
  //     Tool       —— steps 里有成功的工具调用
  //     SQL        —— executed_sql 非空
  //     Validation —— graph.path 含 'validate' 或 'reflect'（给出 validation.ok）
  //     Answer     —— answer 非空
  //   没有任何一段来自"推测模型在想什么"。拿不到数据时该段显示"未发生"，
  //   而不是编一个好看的流程。
  function buildRail(ans) {
    if (!ans) return [];
    var path = (ans.graph && ans.graph.path) || [];
    var has = function (n) { return path.indexOf(n) >= 0; };
    var steps = ans.steps || [];
    var sql = ans.executed_sql || [];
    var okSteps = steps.filter(function (s) { return s && s.ok; });
    var failed = steps.filter(function (s) { return s && !s.ok; });
    var v = ans.validation || {};
    var retries = typeof ans.retries === 'number' ? ans.retries : 0;

    function state(done, blocked) {
      if (blocked) return 'fail';
      return done ? 'done' : 'skip';
    }

    var rail = [];
    rail.push({ key: 'query', label: '提问', state: 'done',
      note: '收到问题' });

    rail.push({ key: 'retrieve', label: '口径检索', state: state(has('retrieve') || (ans.docs || []).length > 0),
      note: (ans.docs || []).length ? ('命中 ' + ans.docs.length + ' 条口径') : '未检索' });

    rail.push({ key: 'tool', label: '工具取数', state: state(okSteps.length > 0, failed.length > 0 && !okSteps.length),
      note: okSteps.length ? (okSteps.length + ' 次成功') : (failed.length ? (failed.length + ' 次失败') : '未调用') });

    rail.push({ key: 'sql', label: '只读 SQL', state: state(sql.length > 0, failed.length > 0 && !sql.length),
      note: sql.length ? (sql.length + ' 条已执行') : '未执行' });

    rail.push({ key: 'validate', label: '结果校验', state: state(has('validate') || has('reflect')),
      note: v.ok === true ? '通过' : (v.ok === false ? '存在问题' : '—') });

    rail.push({ key: 'answer', label: '回答', state: state(!!(ans.answer && String(ans.answer).trim())),
      note: typeof ans.elapsed_ms === 'number' ? (Math.round(ans.elapsed_ms / 100) / 10 + 's') : '' });

    // 有重试时，在校验段标出来 —— "反思重试"是本项目的核心能力，必须可见
    if (retries > 0) {
      rail[4].note = rail[4].note + ' · 重试 ' + retries + ' 次';
      rail[4].retried = true;
    }
    return rail;
  }

  // 服务端把工具返回压成字符串放在 step.summary 里；这里尽量取出可读字段，
  // 取不到就原样显示（**不解析失败就不要显示**，也不能编造行数）。
  function stepSummary(step) {
    if (!step || !step.summary) return '';
    var raw = String(step.summary);
    if (raw.charAt(0) !== '{') return raw.slice(0, 200);
    try {
      var o = JSON.parse(raw);
      var bits = [];
      if (typeof o.row_count === 'number') bits.push('返回 ' + o.row_count + ' 行');
      if (typeof o.elapsed_ms === 'number') bits.push(o.elapsed_ms + ' ms');
      if (o.error && o.error.code) bits.push('错误 ' + o.error.code);
      return bits.join(' · ') || raw.slice(0, 160);
    } catch (e) {
      return raw.slice(0, 160);
    }
  }

  // MarkdownView：把 renderMarkdown 生成的 vnode 数组渲染出来。
  //
  // !! 为什么用 render 函数而不是在模板里 <component :is="node"> !!
  //   模板里要把"vnode 数组"逐个渲染需要嵌套 <component :is>，可读性差，
  //   而且 Vue 对"把 vnode 当 is 传入"的支持有边界情况（数组/片段）。
  //   直接写 render 函数是最朴素的用法：返回 vnode 数组即可。
  //   关键点仍然是**不产生 innerHTML**，所以模型输出里的标签始终是纯文本。
  // ============================================================
  // ---------- 页面 9：数据质量（#/quality） ----------
  // ============================================================
  //
  // !! 这一页的边界，写在最前面 !!
  //   数据服务**没有**质量校验的只读接口，本次改造也不允许新增后端功能。
  //   所以本页**不显示任何 PASS / FAIL / 通过率** —— 那些结论只有 CLI 引擎
  //   跑出来才有；前端自己算或直接写死都等于伪造。
  //
  //   本页展示的三块内容全部是**真实可核**的：
  //     ① 校验清单（25 条）—— checks.conf 的静态镜像，标出域与执行引擎；
  //     ② 实时完整性信号 —— 取自 GET /batch/reconcile：
  //        is_pass / matched / mismatched 是**服务端算出来**的真实结论；
  //     ③ 权限边界 —— 取自 GET /meta/tables：数据服务实际放行的表白名单。
  //   以及一块"怎么跑"的说明（命令 + 退出码语义）。
  var QualityPanel = {
    name: 'QualityPanel',
    template: '#tpl-quality',
    setup: function () {
      var panel = usePanel();
      var reconcile = ref(null);
      var tables = ref([]);
      var filter = ref('all');

      function load() {
        return panel.run(function () {
          // 两条都容错：任一条失败不影响另一块（质量页不该被单个接口打空）
          var rec = api.batchReconcile().then(
            function (d) { reconcile.value = d || null; return d; },
            function () { reconcile.value = null; return null; }
          );
          var tb = api.metaTables().then(
            function (d) { tables.value = Array.isArray(d) ? d : []; return d; },
            function () { tables.value = []; return null; }
          );
          return Promise.all([rec, tb]).then(function () { return true; });
        }, function () { return null; });
      }
      panel.setReload(load);
      onMounted(load);

      var groups = computed(function () {
        if (filter.value === 'all') return QUALITY_GROUPS;
        return QUALITY_GROUPS.filter(function (g) { return g.domain === filter.value; });
      });

      var totalChecks = computed(function () {
        return QUALITY_GROUPS.reduce(function (n, g) { return n + g.checks.length; }, 0);
      });

      var filters = computed(function () {
        return [{ key: 'all', label: '全部（' + totalChecks.value + '）' }].concat(
          QUALITY_GROUPS.map(function (g) {
            return { key: g.domain, label: g.label + '（' + g.checks.length + '）' };
          })
        );
      });

      // 实时完整性信号：**直接用服务端返回的字段**，不做二次判断。
      var integrity = computed(function () {
        var r = reconcile.value;
        var l = r && r.latest;
        if (!l) return null;
        var pass = l.is_pass === true || l.is_pass === 1 || l.is_pass === '1';
        var totals = (r && r.totals) || {};
        var rt = totals.realtime || {};
        var bt = totals.batch || {};
        return {
          pass: pass,
          batchId: l.batch_id,
          comparedAt: l.compared_at,
          windows: l.batch_windows,
          matched: l.matched_windows,
          mismatched: l.mismatched_windows,
          gmvRt: rt.gmv,
          gmvBt: bt.gmv,
          gmvEqual: !isMissing(rt.gmv) && !isMissing(bt.gmv) && moneyToCents(rt.gmv) === moneyToCents(bt.gmv)
        };
      });

      var tableCount = computed(function () { return tables.value.length; });

      // 按库分组：metaTables 的行带 schema 字段（ecommerce = 实时链路库，
      // lakehouse_ads = 离线链路库）。这是**数据服务实际放行的白名单**，
      // 不是把 checks.conf 抄一遍 —— 它回答的是"Agent 到底能读到哪些表"。
      var tablesBySchema = computed(function () {
        var out = {};
        tables.value.forEach(function (r) {
          var s = (r && r.schema) || '（未标注库）';
          if (!out[s]) out[s] = [];
          out[s].push(r);
        });
        return out;
      });
      var schemaNames = computed(function () { return Object.keys(tablesBySchema.value).sort(); });

      return {
        loading: panel.loading,
        error: panel.error,
        groups: groups,
        filters: filters,
        filter: filter,
        totalChecks: totalChecks,
        integrity: integrity,
        tables: tables,
        tableCount: tableCount,
        tablesBySchema: tablesBySchema,
        schemaNames: schemaNames,
        formatInt: formatInt,
        formatMoney: formatMoney,
        formatTime: formatTime
      };
    }
  };

  // ============================================================
  // ---------- 页面 10：实时链路（#/realtime） ----------
  // ============================================================
  //
  // 与「交易分析」「流量分析」的分工（避免做成第三份重复页面）：
  //   #/trade / #/traffic —— 按**指标主题**看历史序列（业务视角）
  //   #/realtime         —— 按**链路健康**看实时侧（工程视角）：
  //                        窗口新鲜度、窗口密度、跨窗口不可加指标的提醒
  // 数据全部来自已有的 /metrics/trade 与 /metrics/traffic，**不新增接口、不造数**。
  var RealtimePanel = {
    name: 'RealtimePanel',
    template: '#tpl-realtime',
    setup: function () {
      var panel = usePanel();
      var trade = ref([]);
      var traffic = ref([]);
      var tradeEl = ref(null);

      function load() {
        return panel.run(function () {
          return Promise.all([api.trade(120), api.traffic(120)]).then(function (res) {
            trade.value = res[0] || [];
            traffic.value = res[1] || [];
            return true;
          });
        }, function () { return null; });
      }
      panel.setReload(load);
      onMounted(load);

      // 新鲜度：最新窗口距"现在"多久。
      // 判据说明：实时链路是 1 分钟窗口，正常情况下滞后应是**分钟级**；
      // 这里只**如实显示**测得的滞后，不设通过阈值 ——
      // 阈值属于告警策略（在 Prometheus 侧），前端不代替它下结论。
      var freshness = computed(function () {
        var rows = trade.value;
        if (!rows.length) return null;
        var last = rows[rows.length - 1];
        var ts = last.window_start;
        if (isMissing(ts)) return null;
        var d = new Date(String(ts).replace(' ', 'T'));
        if (isNaN(d.getTime())) return null;
        var lagMin = Math.floor((Date.now() - d.getTime()) / 60000);
        return { windowStart: ts, lagMin: lagMin };
      });

      var latest = computed(function () {
        var rows = trade.value;
        return rows.length ? rows[rows.length - 1] : null;
      });

      var latestTraffic = computed(function () {
        var rows = traffic.value;
        return rows.length ? rows[rows.length - 1] : null;
      });

      // 窗口密度：最近 N 个窗口里"有业务量"的比例。
      // 为什么值得看：实时链路在数据稀疏时会出现大量 count=0 的窗口，
      // 那不是故障，但会让人误判"链路没在写"。把比例显式写出来，
      // 比让人从趋势图的锯齿里猜要好。
      var density = computed(function () {
        var rows = trade.value;
        if (!rows.length) return null;
        var active = rows.filter(function (r) {
          return numberOrNull(r.order_cnt) > 0 || numberOrNull(r.gmv) > 0;
        }).length;
        return { active: active, total: rows.length, pct: rows.length ? (active / rows.length * 100) : 0 };
      });

      // 实时 KPI：最近一个窗口的真实取值（不是累计值 —— 累计值在交易页）
      var windowKpi = computed(function () {
        var t = latest.value || {};
        var f = latestTraffic.value || {};
        return [
          { label: '本窗口 GMV', value: formatMoney(t.gmv), unit: '元', domain: 'trade', sub: '窗口 ' + formatTime(t.window_start), tip: definitionOf('gmv') },
          { label: '本窗口订单量', value: formatInt(t.order_cnt), unit: '笔', domain: 'trade', sub: '下单用户 ' + formatInt(t.order_user_cnt) + ' 人', tip: definitionOf('order_cnt') },
          { label: '本窗口支付笔数', value: formatInt(t.payment_cnt), unit: '笔', domain: 'trade', sub: '支付金额 ' + formatMoney(t.payment_amount) + ' 元', tip: definitionOf('payment_cnt') },
          { label: '本窗口 PV', value: formatInt(f.pv), unit: '次', domain: 'traffic', sub: '窗口 ' + formatTime(f.window_start), tip: definitionOf('pv') }
        ];
      });

      bindChart(function () { return tradeEl.value; }, function () {
        var rows = trade.value.slice(-60);
        if (!rows.length) return null;
        var labels = rows.map(function (r) { return shortTime(r.window_start); });
        return {
          color: [C.trade, C.neutral],
          tooltip: {
            axisPointer: { type: 'cross', label: { backgroundColor: '#1b2739', color: C.text2, crossStyle: { color: C.grid } } }
          },
          legend: { data: ['GMV', '订单量'] },
          xAxis: timeAxis(labels),
          yAxis: [moneyAxis(), intAxis({ splitLine: { show: false } })],
          series: [
            makeLine('GMV', rows.map(function (r) { return numberOrNull(r.gmv); }), C.trade, 0),
            makeBar('订单量', rows.map(function (r) { return numberOrNull(r.order_cnt); }), withAlpha(C.neutral, 0.75), { yAxisIndex: 1 })
          ]
        };
      });

      return {
        loading: panel.loading,
        error: panel.error,
        trade: trade,
        traffic: traffic,
        freshness: freshness,
        density: density,
        windowKpi: windowKpi,
        tradeEl: tradeEl,
        fmtInt: formatInt,
        fmtMoney: formatMoney,
        fmtTime: formatTime,
        fmtShort: shortTime
      };
    }
  };

  var MarkdownView = {
    name: 'MarkdownView',
    props: { source: { type: String, default: '' } },
    render: function () {
      return h('div', { class: 'md' }, renderMarkdown(this.source));
    }
  };

  var AskPanel = {
    name: 'AskPanel',
    template: '#tpl-ask',
    setup: function () {
      var panel = usePanel();
      var question = ref('');
      var answer = ref(null);
      var agent = ref(null);
      var agentError = ref('');

      var examples = [
        '最近一周每天的 GMV 是多少？',
        '支付成功率和退款率分别是多少？',
        '哪个类目的 GMV 最高？',
        '这些数据准不准？实时和离线一致吗？'
      ];

      // Agent 健康检查：决定是否显示"暂不可用"提示条
      function loadAgent() {
        return api.agentHealth().then(function (data) {
          agent.value = data || null;
          agentError.value = '';
          return data;
        }).catch(function (cause) {
          agent.value = null;
          agentError.value = (cause && cause.message) || '无法获取 Agent 状态';
          return null;
        });
      }
      onMounted(loadAgent);
      panel.setReload(loadAgent);

      var agentReady = computed(function () {
        var a = agent.value;
        return !!(a && a.llm && a.llm.configured && a.data_api && a.data_api.ok);
      });

      // 把"为什么不可用、怎么修"写清楚，而不是只显示一个灰按钮
      var agentHint = computed(function () {
        if (agentError.value) {
          return agentError.value + '（请确认 data-platform-agent 服务已启动）';
        }
        var a = agent.value;
        if (!a) return '正在检查 Agent 状态…';
        if (!a.data_api || !a.data_api.ok) {
          return 'Agent 无法连接只读数据服务：' + ((a.data_api && a.data_api.detail) || '未知原因') +
            '。请确认 data-platform-api 已启动。';
        }
        if (!a.llm || !a.llm.configured) {
          return 'LLM 未配置：请在服务器 /opt/data-platform/.env 中设置 LLM_API_KEY'
            + '（DeepSeek 平台申请），然后 systemctl restart data-platform-agent。';
        }
        return '';
      });

      function submit() {
        var q = question.value.trim();
        if (!q || panel.loading.value) return;
        answer.value = null;
        return panel.run(function () {
          return api.agentAsk(q).then(function (data) {
            answer.value = data || null;
            return data;
          });
        }, function () { return null; });
      }

      function useExample(q) {
        question.value = q;
        submit();
      }

      // SQL 复制。两级降级：
      //   ① navigator.clipboard（需要 https 或 localhost 安全上下文）
      //   ② 站点是**明文 HTTP + IP 访问**，不是安全上下文，navigator.clipboard
      //      在多数浏览器里不可用 → 退回临时的 textarea + execCommand。
      // 两级都失败时给出可操作的提示，而不是静默什么都不做。
      var copied = ref(-1);
      var copyNote = ref('');
      function copySql(sql, index) {
        var text = String(sql || '');
        var done = function () {
          copied.value = index;
          copyNote.value = '';
          window.setTimeout(function () { copied.value = -1; }, 1600);
        };
        var fallback = function () {
          try {
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', 'readonly');
            ta.style.position = 'fixed';
            ta.style.top = '-1000px';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            var ok = document.execCommand('copy');
            document.body.removeChild(ta);
            if (ok) { done(); return; }
          } catch (e) { /* 落到下面的提示 */ }
          copyNote.value = '当前浏览器不允许自动复制（站点为明文 HTTP），请手动选中 SQL 文本复制。';
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, fallback);
        } else {
          fallback();
        }
      }

      var rail = computed(function () { return buildRail(answer.value); });
      var answerNodes = computed(function () {
        var a = answer.value;
        return a ? renderMarkdown(a.answer) : [];
      });
      var docCount = computed(function () {
        var a = answer.value;
        return a && a.docs ? a.docs.length : 0;
      });
      // 校验结论：validation.ok === false 时要在回答区顶部明确提示，
      // 不能只把问题埋在折叠的"执行过程"里。
      var validationNote = computed(function () {
        var a = answer.value;
        var v = a && a.validation;
        if (!v) return '';
        var blockers = v.blocking_issues || [];
        if (blockers.length) return blockers.join('；');
        var warnings = v.warnings || [];
        if (warnings.length) return warnings.join('；');
        return '';
      });
      var validationOk = computed(function () {
        var a = answer.value;
        return !!(a && a.validation && a.validation.ok);
      });

      return {
        loading: panel.loading,
        error: panel.error,
        question: question,
        answer: answer,
        agent: agent,
        agentReady: agentReady,
        agentHint: agentHint,
        examples: examples,
        submit: submit,
        useExample: useExample,
        prettyArgs: prettyArgs,
        formatMs: formatMs,
        rail: rail,
        answerNodes: answerNodes,
        docCount: docCount,
        validationNote: validationNote,
        validationOk: validationOk,
        stepSummary: stepSummary,
        copied: copied,
        copyNote: copyNote,
        copySql: copySql
      };
    }
  };

  // ============================================================
  // ---------- 根组件 ----------
  // ============================================================

  // 窄屏（<1024px）时侧栏是抽屉：折叠按钮无意义，改由顶栏的菜单按钮控制。
  var narrowQuery = typeof window.matchMedia === 'function'
    ? window.matchMedia('(max-width: 1023px)')
    : null;

  var App = {
    name: 'App',
    setup: function () {
      var page = currentPage;
      var collapsed = ref(false);
      var navOpen = ref(false);
      var isNarrow = ref(narrowQuery ? narrowQuery.matches : false);
      setupRoute();

      function syncNarrow(event) {
        isNarrow.value = event.matches;
        if (!event.matches) navOpen.value = false;   // 放大回桌面时收起抽屉
      }

      onMounted(function () {
        if (!narrowQuery) return;
        if (typeof narrowQuery.addEventListener === 'function') {
          narrowQuery.addEventListener('change', syncNarrow);
        } else if (typeof narrowQuery.addListener === 'function') {
          narrowQuery.addListener(syncNarrow);
        }
      });
      onBeforeUnmount(function () {
        if (!narrowQuery) return;
        if (typeof narrowQuery.removeEventListener === 'function') {
          narrowQuery.removeEventListener('change', syncNarrow);
        } else if (typeof narrowQuery.removeListener === 'function') {
          narrowQuery.removeListener(syncNarrow);
        }
      });

      // 抽屉打开时按 Esc 关闭
      function onEsc(event) {
        if (event.key === 'Escape' && navOpen.value) navOpen.value = false;
      }
      onMounted(function () { document.addEventListener('keydown', onEsc); });
      onBeforeUnmount(function () { document.removeEventListener('keydown', onEsc); });

      var route = computed(function () {
        var key = page.value;
        for (var i = 0; i < ROUTES.length; i++) {
          if (ROUTES[i].key === key) return ROUTES[i];
        }
        return ROUTES[0];
      });

      var anyLoading = computed(function () { return refreshing.value > 0; });
      var updatedText = computed(function () { return latestStamp.value || '尚未加载'; });
      var errorText = computed(function () { return globalError.value; });
      var apiBase = API_BASE;
      var statusText = computed(function () {
        if (errorText.value) return '数据加载失败：' + errorText.value;
        return anyLoading.value ? '正在加载数据' : '数据已就绪';
      });

      // 刷新：逐个调用已登记的面板 reload；面板内部有 loading 互斥，重复点击无副作用
      function refreshAll() {
        globalError.value = '';
        var jobs = reloadHooks.map(function (fn) { return fn(); });
        Promise.all(jobs).catch(function () {
          // 面板各自已经记录了错误信息，这里只需吞掉聚合异常
          return null;
        });
      }

      // 窄屏下"折叠"即关闭抽屉，桌面下才是真正的收起侧栏
      function toggleCollapsed() {
        if (isNarrow.value) {
          navOpen.value = false;
          return;
        }
        collapsed.value = !collapsed.value;
      }

      onMounted(function () { preloadDefinitions(); });

      return {
        routes: ROUTES,
        navGroups: navGroups,
        page: page,
        route: route,
        collapsed: collapsed,
        navOpen: navOpen,
        isNarrow: isNarrow,
        toggleCollapsed: toggleCollapsed,
        anyLoading: anyLoading,
        updatedText: updatedText,
        errorText: errorText,
        statusText: statusText,
        apiBase: apiBase,
        sourceLabel: sourceLabel,
        sourceDetail: sourceDetail,
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
  app.component('markdown-view', MarkdownView);
  app.component('overview-panel', OverviewPanel);
  app.component('trade-panel', TradePanel);
  app.component('traffic-panel', TrafficPanel);
  app.component('category-panel', CategoryPanel);
  app.component('orders-panel', OrdersPanel);
  app.component('metrics-panel', MetricsPanel);
  app.component('batch-panel', BatchPanel);
  app.component('ask-panel', AskPanel);
  app.component('quality-panel', QualityPanel);
  app.component('realtime-panel', RealtimePanel);
  app.mount('#app');

  if (bootEl && bootEl.parentNode) bootEl.parentNode.removeChild(bootEl);
})();
