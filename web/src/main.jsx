import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { useCallback } from "react";
import ReactMarkdown from "react-markdown";
import {
  ArrowDownUp,
  Bell,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clipboard,
  Database,
  BookOpen,
  Newspaper,
  Rocket,
  ExternalLink,
  Filter,
  Gauge,
  Globe2,
  History,
  Info,
  LayoutGrid,
  List,
  Loader2,
  MessageSquare,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Activity,
  Tags,
  TestTubeDiagonal,
  X,
} from "lucide-react";
import "./styles.css";

const PAGE_SIZE = 24;
const DEFAULT_PAGE_SIZES = {
  sites: PAGE_SIZE,
  models: 12,
  announcements: 30,
  news: PAGE_SIZE,
  chat: PAGE_SIZE,
  tools: PAGE_SIZE,
  status: PAGE_SIZE,
  about: PAGE_SIZE,
  detect: PAGE_SIZE,
};
const TOKEN_PRICE_MULTIPLIER = 14;
const REQUEST_PRICE_MULTIPLIER = 7;

const defaultFilters = {
  status: "all",
  provider: "all",
  group: "all",
  billing: "all",
  tag: "all",
  model: "",
  minSuccess: "",
  maxLatency: "",
  minTps: "",
  sort: "random",
};

const SITE_SORTS = new Set(["random", "online", "price", "models", "name"]);
const MODEL_SORTS = new Set(["usd", "cny", "request"]);

const MODEL_SOURCE_NOTE = "数据按站点公开接口采集，部分站点会因访问区域、登录状态或可用分组不同，和站内模型广场显示略有差异；实际可用以目标站点为准。";
const MODEL_SORT_HINTS = {
  usd: ["以美元计价的站点。温馨提示：不少站点结算约为 1 美元 ≈ 1 人民币，实际汇率以各站点支付比率为准。", MODEL_SOURCE_NOTE],
  cny: ["以人民币计价的站点，实际结算以各站点支付比率为准。", MODEL_SOURCE_NOTE],
  request: ["按次计费的站点（每次调用的价格），实际以各站点为准。", MODEL_SOURCE_NOTE],
};

const PREFERRED_CHAT_MODEL = "gpt-5.5";
const PREFERRED_MODEL_PATTERNS = [
  /^gpt-5\.5$/i,
  /gpt[-_.\s]*5\.5/i,
  /^gpt-5/i,
  /gpt[-_.\s]*5/i,
  /claude.*opus/i,
  /claude.*sonnet/i,
  /gemini.*pro/i,
  /deepseek.*r1/i,
  /deepseek.*v3/i,
  /gpt[-_.\s]*4\.?1/i,
  /gpt[-_.\s]*4o/i,
];

function preferredModelFromList(models = [], fallback = PREFERRED_CHAT_MODEL) {
  const list = (models || []).filter(Boolean);
  for (const pattern of PREFERRED_MODEL_PATTERNS) {
    const matched = list.find((item) => pattern.test(item));
    if (matched) return matched;
  }
  return list[0] || fallback;
}

function activeSiteSort(sort) {
  return SITE_SORTS.has(sort) ? sort : "random";
}

function activeModelSort(sort) {
  return MODEL_SORTS.has(sort) ? sort : "usd";
}

function msUntilNextSummaryRefresh(now = new Date()) {
  const refreshMinutes = [12, 32, 52];
  const next = new Date(now);
  next.setMilliseconds(0);
  next.setSeconds(15);
  const currentMinute = now.getMinutes();
  const targetMinute = refreshMinutes.find((minute) => minute > currentMinute || (minute === currentMinute && now.getSeconds() < 15));
  if (targetMinute === undefined) {
    next.setHours(now.getHours() + 1, refreshMinutes[0], 15, 0);
  } else {
    next.setMinutes(targetMinute);
  }
  return Math.max(5_000, next.getTime() - now.getTime());
}

const API_TIMEOUT_MS = 25000;

function messageFromUnknown(value, fallback = "请求失败") {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        return messageFromUnknown(JSON.parse(trimmed), fallback);
      } catch {
        return value;
      }
    }
    return value;
  }
  if (value instanceof Error) return messageFromUnknown(value.message, fallback);
  if (typeof value === "object") {
    for (const key of ["message", "msg", "detail", "error", "reason"]) {
      if (value[key] !== undefined && value[key] !== null && value[key] !== "") {
        return messageFromUnknown(value[key], fallback);
      }
    }
    try {
      return JSON.stringify(value);
    } catch {
      return fallback;
    }
  }
  return String(value);
}

function api(path, params = {}, options = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  });
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  if (options.signal) {
    if (options.signal.aborted) {
      controller.abort();
    } else {
      options.signal.addEventListener("abort", abortFromCaller, { once: true });
    }
  }
  const timer = window.setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  return fetch(url, { signal: controller.signal })
    .then((response) => {
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
    })
    .catch((error) => {
      if (error.name === "AbortError" && options.signal?.aborted) {
        const aborted = new Error("请求已取消");
        aborted.name = "AbortError";
        throw aborted;
      }
      if (error.name === "AbortError") throw new Error("请求超时，请稍后重试");
      throw error;
    })
    .finally(() => {
      window.clearTimeout(timer);
      options.signal?.removeEventListener("abort", abortFromCaller);
    });
}

function postApi(path, payload = {}, options = {}) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), options.timeoutMs || API_TIMEOUT_MS);
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: controller.signal,
  })
    .then((response) => {
      if (!response.ok) {
        return response.json().catch(() => ({})).then((data) => {
          throw new Error(messageFromUnknown(data.detail || data, `${response.status} ${response.statusText}`));
        });
      }
      return response.json();
    })
    .catch((error) => {
      if (error.name === "AbortError") throw new Error("请求超时，请稍后重试");
      throw new Error(messageFromUnknown(error, "请求失败"));
    })
    .finally(() => window.clearTimeout(timer));
}

function deleteApi(path, adminToken = "") {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  return fetch(path, {
    method: "DELETE",
    headers: adminToken ? { "X-Admin-Token": adminToken } : {},
    signal: controller.signal,
  })
    .then((response) => {
      if (!response.ok) {
        return response.json().catch(() => ({})).then((data) => {
          throw new Error(messageFromUnknown(data.detail || data, `${response.status} ${response.statusText}`));
        });
      }
      return response.json();
    })
    .catch((error) => {
      if (error.name === "AbortError") throw new Error("请求超时，请稍后重试");
      throw new Error(messageFromUnknown(error, "请求失败"));
    })
    .finally(() => window.clearTimeout(timer));
}

function statusText(status) {
  if (status === "online") return "在线";
  if (status === "partial") return "部分可读";
  if (status === "unknown") return "未采集";
  return "未知";
}

function displayProvider(value) {
  return value === "Other" ? "未知供应商" : value;
}

function ratioText(value) {
  if (value === null || value === undefined) return "未知";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 6 });
}

function numericValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function priceValueText(value) {
  if (value === null || value === undefined) return "-";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 6 });
}

function compactNumberText(value) {
  const number = numericValue(value);
  if (number === null) return "-";
  if (number === 0) return "0";
  const abs = Math.abs(number);
  if (abs < 0.000001) return number.toFixed(8).replace(/0+$/, "").replace(/\.$/, "");
  if (abs < 0.001) return number.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  if (abs < 1) return number.toLocaleString("zh-CN", { maximumSignificantDigits: 4 });
  if (abs < 100) return number.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return number.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function moneyText(value, unit, symbol = "¥", compact = false) {
  if (value === null || value === undefined) return "-";
  return `${symbol || "¥"}${compact ? compactNumberText(value) : priceValueText(value)}/${unit}`;
}

function ratioUnitText(value, compact = false) {
  if (value === null || value === undefined) return "-";
  if (compact) return `${compactNumberText(value)}x`;
  return `${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}x`;
}

function multipliedPrice(base, multiplier) {
  const numericBase = numericValue(base);
  const numericMultiplier = numericValue(multiplier);
  if (numericBase === null || numericMultiplier === null) return null;
  return numericBase * numericMultiplier;
}

function groupRatioForModel(item, group) {
  if (!item) return 1;
  const ratios = item.group_ratios || {};
  const groupRatio = group ? numericValue(ratios[group]) : null;
  if (groupRatio !== null) return groupRatio;
  const minGroupRatio = numericValue(item.min_group_ratio);
  return minGroupRatio === null ? 1 : minGroupRatio;
}

function effectiveModelRatio(item, group) {
  return multipliedPrice(item?.model_ratio, groupRatioForModel(item, group));
}

function outputPriceValue(item, group) {
  return multipliedPrice(inputPriceValue(item, group), item?.completion_ratio);
}

function cacheInputPriceValue(item, group) {
  return multipliedPrice(inputPriceValue(item, group), item?.cache_ratio);
}

function cacheWritePriceValue(item, group) {
  return multipliedPrice(inputPriceValue(item, group), item?.create_cache_ratio);
}

function inputPriceValue(item, group) {
  return multipliedPrice(effectiveModelRatio(item, group), item?.token_price_multiplier ?? TOKEN_PRICE_MULTIPLIER);
}

function requestPriceValue(item, group) {
  return multipliedPrice(multipliedPrice(item?.model_price, groupRatioForModel(item, group)), item?.request_price_multiplier ?? REQUEST_PRICE_MULTIPLIER);
}

function currencySymbol(item) {
  return item?.currency_symbol || "¥";
}

function priceText(item) {
  if (!item) return "未知";
  if (item.model_price && Number(item.model_price) > 0) return moneyText(requestPriceValue(item), "次", currencySymbol(item));
  return `输入 ${moneyText(inputPriceValue(item), "1M", currencySymbol(item))} / 输出 ${moneyText(outputPriceValue(item), "1M", currencySymbol(item))}`;
}

function billingText(item) {
  if (!item) return "未知";
  if (item.model_price && Number(item.model_price) > 0) return "按次";
  return "按量";
}

function isRequestBilled(item) {
  return !!(item?.model_price && Number(item.model_price) > 0);
}

function multiplierText(item, group, compact = false) {
  if (!item || (item.model_price && Number(item.model_price) > 0)) return "-";
  return ratioUnitText(groupRatioForModel(item, group), compact);
}

function usagePriceText(item, value, group, compact = false) {
  if (item?.model_price && Number(item.model_price) > 0) return moneyText(requestPriceValue(item, group), "次", currencySymbol(item), compact);
  return moneyText(value, "1M", currencySymbol(item), compact);
}

function modelPriceValueText(item, type, group, compact = false) {
  if (isRequestBilled(item)) {
    return type === "request" ? moneyText(requestPriceValue(item, group), "次", currencySymbol(item), compact) : "-";
  }
  if (type === "input") return usagePriceText(item, inputPriceValue(item, group), group, compact);
  if (type === "output") return usagePriceText(item, outputPriceValue(item, group), group, compact);
  if (type === "cache_input") return usagePriceText(item, cacheInputPriceValue(item, group), group, compact);
  if (type === "cache_write") return usagePriceText(item, cacheWritePriceValue(item, group), group, compact);
  return "-";
}

function pricedGroupEntries(item, limit = 1) {
  const entries = Object.entries(item?.group_ratios || {})
    .filter(([name]) => name)
    .sort((a, b) => {
      const left = numericValue(a[1]);
      const right = numericValue(b[1]);
      if (left === null && right === null) return a[0].localeCompare(b[0]);
      if (left === null) return 1;
      if (right === null) return -1;
      return left - right || a[0].localeCompare(b[0]);
    });
  const activeRatio = numericValue(groupRatioForModel(item));
  const pricedEntries =
    activeRatio === null
      ? entries
      : entries.filter(([, ratio]) => {
          const value = numericValue(ratio);
          return value !== null && Math.abs(value - activeRatio) < 0.000001;
        });
  return {
    visible: pricedEntries.slice(0, limit),
    hidden: Math.max(0, pricedEntries.length - limit),
  };
}

function pricedGroupText(item, limit = 80) {
  const groups = pricedGroupEntries(item, limit);
  if (!groups.visible.length) return "-";
  const names = groups.visible.map(([name]) => name);
  if (groups.hidden > 0) names.push(`+${groups.hidden}`);
  return names.join(" / ");
}

function visibleGroupEntries(item, limit = 1) {
  const entries = allGroupEntries(item).filter(([name]) => name !== "默认" || Object.keys(item?.group_ratios || {}).length === 0);
  return {
    visible: entries.slice(0, limit),
    hidden: Math.max(0, entries.length - limit),
  };
}

function visibleGroupText(item, limit = 80) {
  const groups = visibleGroupEntries(item, limit);
  if (!groups.visible.length) return "-";
  const names = groups.visible.map(([name]) => name);
  if (groups.hidden > 0) names.push(`+${groups.hidden}`);
  return names.join(" / ");
}

function allGroupEntries(item) {
  const entries = Object.entries(item?.group_ratios || {})
    .filter(([name]) => name)
    .sort((a, b) => {
      const left = numericValue(a[1]);
      const right = numericValue(b[1]);
      if (left === null && right === null) return a[0].localeCompare(b[0]);
      if (left === null) return 1;
      if (right === null) return -1;
      return left - right || a[0].localeCompare(b[0]);
    });
  return entries.length ? entries : [["默认", null]];
}

function timeText(value) {
  if (!value) return "未知时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function useDebounced(value, delay = 220) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function App() {
  const [view, setView] = useState("sites");
  const [layout, setLayout] = useState("grid");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSizes, setPageSizes] = useState(DEFAULT_PAGE_SIZES);
  const [filters, setFilters] = useState(defaultFilters);
  const [summary, setSummary] = useState(null);
  const [officialStatus, setOfficialStatus] = useState(null);
  const [aiNews, setAiNews] = useState(null);
  const [aiNewsCategory, setAiNewsCategory] = useState("all");
  const [officialProviderId, setOfficialProviderId] = useState("openai");
  const [filterMeta, setFilterMeta] = useState(null);
  const [data, setData] = useState({ items: [], total: 0, pages: 1, page: 1 });
  const [loading, setLoading] = useState(true);
  const [drawerSite, setDrawerSite] = useState(null);
  const [drawerFocus, setDrawerFocus] = useState({ type: "all" });
  const [priceDrawer, setPriceDrawer] = useState(null);
  const [pendingSubmitScroll, setPendingSubmitScroll] = useState(false);
  const debouncedQuery = useDebounced(query);
  const debouncedModelFilter = useDebounced(filters.model);
  const debouncedMinSuccess = useDebounced(filters.minSuccess);
  const debouncedMaxLatency = useDebounced(filters.maxLatency);
  const debouncedMinTps = useDebounced(filters.minTps);
  const requestSeq = useRef(0);
  const dataRequestController = useRef(null);
  const currentPageSize = pageSizes[view] || PAGE_SIZE;
  const visibleData = data._view === view ? data : { items: [], total: 0, pages: 1, page: 1, page_size: currentPageSize };
  const activeFilters = {
    ...filters,
    model: debouncedModelFilter,
    minSuccess: debouncedMinSuccess,
    maxLatency: debouncedMaxLatency,
    minTps: debouncedMinTps,
  };

  const refreshSummary = useCallback(() => {
    api("/api/summary").then(setSummary).catch(() => {});
    api("/api/filters").then(setFilterMeta).catch(() => {});
  }, []);

  const refreshScheduledData = useCallback(() => {
    refreshSummary();
    api("/api/ai-news").then(setAiNews).catch(() => {});
  }, [refreshSummary]);

  useEffect(() => {
    refreshScheduledData();
    api("/api/official-status/summary").then(setOfficialStatus).catch(() => {});
  }, [refreshScheduledData]);

  useEffect(() => {
    let timer = null;
    const schedule = () => {
      timer = window.setTimeout(() => {
        refreshScheduledData();
        schedule();
      }, msUntilNextSummaryRefresh());
    };
    schedule();
    return () => {
      if (timer) window.clearTimeout(timer);
    };
  }, [refreshScheduledData]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      refreshScheduledData();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refreshScheduledData]);

  useEffect(() => () => dataRequestController.current?.abort(), []);

  useEffect(() => {
    setPage(1);
  }, [view, debouncedQuery, filters.status, filters.provider, filters.group, filters.billing, filters.tag, debouncedModelFilter, debouncedMinSuccess, debouncedMaxLatency, debouncedMinTps, filters.sort, currentPageSize]);

  useEffect(() => {
    if (!filterMeta || filters.tag === "all") return;
    const source = view === "announcements" ? filterMeta.announcement_tags : filterMeta.tags;
    const hasCurrentTag = (source || []).some((item) => item.value === filters.tag);
    if (!hasCurrentTag) {
      setFilters((current) => ({ ...current, tag: "all" }));
    }
  }, [view, filterMeta, filters.tag]);

  useEffect(() => {
    loadData();
  }, [view, page, debouncedQuery, filters.status, filters.provider, filters.group, filters.billing, filters.tag, filters.sort, debouncedModelFilter, debouncedMinSuccess, debouncedMaxLatency, debouncedMinTps, currentPageSize]);

  useEffect(() => {
    if (view !== "status") return;
    api("/api/official-status").then(setOfficialStatus).catch(() => {});
  }, [view]);

  useEffect(() => {
    if (view !== "about" || !pendingSubmitScroll) return;
    let attempt = 0;
    let timer = null;
    const scroll = () => {
      const target = document.getElementById("submit-site");
      if (target) {
        const topbarHeight = document.querySelector(".topbar")?.getBoundingClientRect().height || 0;
        const y = target.getBoundingClientRect().top + window.scrollY - topbarHeight - 12;
        const nextTop = Math.max(0, y);
        const scroller = document.scrollingElement || document.documentElement;
        scroller.scrollTop = nextTop;
        document.body.scrollTop = nextTop;
        window.scrollTo({ top: nextTop, behavior: "smooth" });
        window.history.replaceState(null, "", "#submit-site");
        attempt += 1;
        if (attempt >= 8) {
          setPendingSubmitScroll(false);
          return;
        }
        timer = window.setTimeout(scroll, 120);
        return;
      }
      attempt += 1;
      if (attempt >= 20) {
        setPendingSubmitScroll(false);
        return;
      }
      timer = window.setTimeout(scroll, 50);
    };
    timer = window.setTimeout(scroll, 50);
    return () => window.clearTimeout(timer);
  }, [view, pendingSubmitScroll]);

  function loadData() {
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    dataRequestController.current?.abort();
    if (view === "about" || view === "detect" || view === "status" || view === "news" || view === "chat" || view === "tools") {
      setLoading(false);
      setData({ _view: view, items: [{ id: view }], total: 0, pages: 1, page: 1, page_size: currentPageSize });
      return;
    }
    const controller = new AbortController();
    dataRequestController.current = controller;
    setLoading(true);
    const base = { q: debouncedQuery, page, page_size: currentPageSize };
    const request =
      view === "sites"
        ? api("/api/sites", {
            ...base,
            status: filters.status,
            provider: filters.provider,
            group: filters.group,
            billing: filters.billing,
            model: debouncedModelFilter,
              sort: activeSiteSort(filters.sort),
          }, { signal: controller.signal })
        : view === "models"
          ? api("/api/models", {
              ...base,
              q: debouncedModelFilter || debouncedQuery,
              provider: filters.provider,
              min_success: debouncedMinSuccess,
              max_latency: debouncedMaxLatency ? Number(debouncedMaxLatency) * 1000 : "",
              min_tps: debouncedMinTps,
              sort: activeModelSort(filters.sort),
            }, { signal: controller.signal })
          : api("/api/announcements", { ...base, tag: filters.tag }, { signal: controller.signal });

    request
      .then((nextData) => {
        if (requestId === requestSeq.current) {
          setData({ ...nextData, _view: view });
        }
      })
      .catch((error) => {
        if (error.name === "AbortError") return;
        if (requestId === requestSeq.current) {
          setData({ _view: view, items: [], total: 0, pages: 1, page: 1, error: true, errorMessage: messageFromUnknown(error, "加载失败，请刷新重试") });
        }
      })
      .finally(() => {
        if (requestId === requestSeq.current) {
          dataRequestController.current = null;
          setLoading(false);
        }
      });
  }

  function updateFilter(key, value) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function resetFilters() {
    setQuery("");
    setFilters(defaultFilters);
    setPage(1);
  }

  async function openSite(siteId, focus = { type: "all" }) {
    const site = await api(`/api/sites/${siteId}`);
    setPriceDrawer(null);
    setDrawerFocus(focus);
    setDrawerSite(site);
  }

  function jumpToSubmitSite() {
    setView("about");
    setPendingSubmitScroll(true);
  }

  function openOfficialStatus(providerId) {
    if (providerId) setOfficialProviderId(providerId);
    setView("status");
  }

  function refreshVisibleData() {
    loadData();
    refreshSummary();
    api(view === "status" ? "/api/official-status" : "/api/official-status/summary", view === "status" ? { force: 1 } : {}).then(setOfficialStatus).catch(() => {});
    if (view === "news") api("/api/ai-news", { force: 1 }).then(setAiNews).catch(() => {});
  }

  return (
    <div className="app">
      <a className="crawler-trap-link" href="/api/_crawler_trap" tabIndex="-1" aria-hidden="true">crawler</a>
      <Topbar query={query} setQuery={setQuery} view={view} setView={setView} onSubmitSiteClick={jumpToSubmitSite} onRefresh={refreshVisibleData} loading={loading} />
      <Overview summary={summary} officialStatus={officialStatus} openOfficialStatus={openOfficialStatus} />
      <main className={`shell ${["chat", "tools"].includes(view) ? "chat-shell" : ""}`}>
        {!["chat", "tools"].includes(view) && (
          <Sidebar
            view={view}
            filters={filters}
            updateFilter={updateFilter}
            resetFilters={resetFilters}
            filterMeta={filterMeta}
            aiNews={aiNews}
            aiNewsCategory={aiNewsCategory}
            setAiNewsCategory={setAiNewsCategory}
          />
        )}
        <section className="main">
          {view !== "news" && view !== "chat" && view !== "tools" && (
            <Toolbar view={view} layout={layout} setLayout={setLayout} data={visibleData} loading={loading} sortHint={view === "models" ? MODEL_SORT_HINTS[activeModelSort(filters.sort)] : null} />
          )}
          <Content view={view} layout={layout} data={visibleData} loading={loading} openSite={openSite} openPriceDrawer={setPriceDrawer} modelSort={activeModelSort(filters.sort)} filters={activeFilters} summary={summary} officialStatus={officialStatus} aiNews={aiNews} newsQuery={debouncedQuery} reloadAiNews={() => api("/api/ai-news", { force: 1 }).then(setAiNews)} aiNewsCategory={aiNewsCategory} setAiNewsCategory={setAiNewsCategory} officialProviderId={officialProviderId} setOfficialProviderId={setOfficialProviderId} reloadOfficialStatus={() => api("/api/official-status", { force: 1 }).then(setOfficialStatus)} />
          {view !== "about" && view !== "detect" && view !== "status" && view !== "news" && view !== "chat" && view !== "tools" && (
            <Pager
              page={visibleData.page || page}
              pages={visibleData.pages || 1}
              pageSize={visibleData.page_size || currentPageSize}
              total={visibleData.total || 0}
              setPage={setPage}
              setPageSize={(nextSize) => setPageSizes((current) => ({ ...current, [view]: nextSize }))}
            />
          )}
        </section>
      </main>
      <SiteFooter setView={setView} />
      <SiteDrawer site={drawerSite} focus={drawerFocus} onClearFocus={() => setDrawerFocus({ type: "all" })} onClose={() => setDrawerSite(null)} />
      <PriceDrawer detail={priceDrawer} onClose={() => setPriceDrawer(null)} />
    </div>
  );
}

function SiteFooter({ setView }) {
  function go(nextView, hash = "") {
    setView(nextView);
    window.setTimeout(() => {
      if (hash) {
        const target = document.querySelector(hash);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      window.scrollTo({ top: 0, behavior: "smooth" });
    }, 0);
  }

  const columns = [
    {
      title: "产品",
      links: [
        ["站点聚合", () => go("sites")],
        ["模型比价", () => go("models")],
        ["站点工具", () => go("tools")],
        ["官方状态", () => go("status")],
        ["AI 资讯", () => go("news")],
        ["公告流", () => go("announcements")],
        ["关于本站", () => go("about")],
      ],
    },
    {
      title: "数据说明",
      links: [
        ["数据来源", () => go("about", "#about-data")],
        ["AI 资讯来源", () => go("news", "#ai-news-sources")],
        ["比价口径", () => go("about", "#about-pricing")],
        ["更新机制", () => go("about", "#about-refresh")],
        ["使用边界", () => go("about", "#about-boundary")],
      ],
    },
    {
      title: "友情链接",
      links: [
        ["New API", "https://docs.newapi.pro"],
        ["OpenAI Platform", "https://platform.openai.com"],
        ["Anthropic", "https://www.anthropic.com"],
        ["Google AI", "https://ai.google.dev"],
        ["DeepSeek", "https://www.deepseek.com"],
        ["Qwen", "https://qwenlm.github.io"],
      ],
    },
    {
      title: "联系与反馈",
      links: [
        ["提交站点", () => go("about", "#submit-site")],
        ["数据纠错说明", () => go("about", "#about-boundary")],
        ["站点收录说明", () => go("about", "#about-data")],
        ["项目说明", () => go("about")],
      ],
    },
  ];
  return (
    <footer className="app-footer">
      <div className="footer-main">
        <div className="footer-brand">
          <div className="footer-logo"><Gauge size={22} /></div>
          <div>
            <strong>RelayWatch</strong>
            <p>AI 中转站聚合、模型比价与公告追踪。</p>
          </div>
        </div>
        <div className="footer-columns">
          {columns.map((column) => (
            <div className="footer-column" key={column.title}>
              <h3>{column.title}</h3>
              {column.links.map(([label, action]) =>
                typeof action === "string" ? (
                  <a key={label} href={action} target="_blank" rel="noreferrer">{label}</a>
                ) : (
                  <button key={label} type="button" onClick={action}>{label}</button>
                ),
              )}
            </div>
          ))}
        </div>
        <div className="footer-bottom">
          <span>© RelayWatch</span>
          <span>公开数据整理</span>
          <span>仅作信息参考</span>
          <span>欢迎反馈纠错</span>
        </div>
      </div>
    </footer>
  );
}

function Topbar({ query, setQuery, view, setView, onSubmitSiteClick, onRefresh, loading }) {
  return (
    <header className="topbar">
      <a className="brand" href="/" title="返回首页" aria-label="RelayWatch 首页">
        <div className="brand-mark"><Gauge size={22} /></div>
        <div>
          <h1>RelayWatch</h1>
          <p>AI 中转站聚合与比价</p>
        </div>
      </a>
      <nav className="view-tabs">
        <NavButton active={view === "sites"} onClick={() => setView("sites")} icon={Server}>站点聚合</NavButton>
        <NavButton active={view === "models"} onClick={() => setView("models")} icon={Sparkles}>模型比价</NavButton>
        <NavButton active={["tools", "detect", "chat"].includes(view)} onClick={() => setView("tools")} icon={SlidersHorizontal}>站点工具</NavButton>
        <NavButton active={view === "announcements"} onClick={() => setView("announcements")} icon={Bell}>公告流</NavButton>
        <NavButton active={view === "news"} onClick={() => setView("news")} icon={Newspaper}>AI 资讯</NavButton>
        <NavButton active={view === "about"} onClick={() => setView("about")} icon={Info}>关于本站</NavButton>
      </nav>
      <div className="top-search">
        <Search size={17} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索站点、模型、分组或公告" />
      </div>
      <button className="submit-shortcut" type="button" onClick={onSubmitSiteClick}>
        <Send size={16} />
        提交站点
      </button>
      <button className="icon-button" type="button" onClick={onRefresh} title="刷新">
        <RefreshCw className={loading ? "spin" : ""} size={17} />
      </button>
    </header>
  );
}

function NavButton({ active, onClick, icon: Icon, children }) {
  return <button type="button" className={`nav-item ${active ? "active" : ""}`} onClick={onClick}><Icon size={16} />{children}</button>;
}

function Overview({ summary, officialStatus, openOfficialStatus }) {
  return (
    <>
      <section className="overview">
        <Metric icon={Globe2} label="站点总数" value={summary?.sites ?? "-"} />
        <Metric icon={CheckCircle2} label="在线站点" value={summary?.online_sites ?? "-"} tone="good" />
        <Metric icon={Sparkles} label="可比价模型" value={summary?.models ?? "-"} />
        <Metric icon={Bell} label="公告记录" value={summary?.announcements ?? "-"} tone="warm" />
      </section>
      <OfficialStatusStrip status={officialStatus} onOpen={openOfficialStatus} />
      <RainyunAd />
    </>
  );
}

function RainyunAd() {
  return (
    <section className="sponsor-strip" aria-label="推广">
      <div className="sponsor-inner">
        <div className="sponsor-copy">
          <span>推广</span>
          <strong>雨云首月 5 折</strong>
          <p>注册即送优惠券，云服务器、云应用、游戏云、对象存储等产品可选，适合网站部署和轻量服务托管。</p>
        </div>
        <a className="sponsor-link" href="https://www.rainyun.com/rain666_" target="_blank" rel="noreferrer">
          查看优惠 <ExternalLink size={14} />
        </a>
      </div>
    </section>
  );
}

function Metric({ icon: Icon, label, value, helper, tone }) {
  return (
    <article className={`metric ${tone || ""}`}>
      <div className="metric-icon"><Icon size={18} /></div>
      <div>
        <strong>{value}</strong>
        <span>{label}</span>
        {helper && <p>{helper}</p>}
      </div>
    </article>
  );
}

function statusTone(indicator) {
  if (indicator === "none") return "ok";
  if (indicator === "minor" || indicator === "maintenance") return "warn";
  if (indicator === "major" || indicator === "critical") return "bad";
  return "unknown";
}

function officialStatusText(provider) {
  return provider?.status_label || "未知";
}

function safeDisplayText(value, fallback = "") {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function normalizedNewsSourceName(value) {
  const name = safeDisplayText(value, "").trim();
  if (!name) return "";
  const lowered = name.toLowerCase();
  if (lowered.startsWith("linuxdo")) return "LinuxDo";
  if (lowered.startsWith("v2ex")) return "V2EX";
  if (lowered === "openai news") return "OpenAI";
  if (lowered.startsWith("anthropic")) return "Anthropic";
  return name;
}

function providerStatusSummary(provider) {
  if (provider?.indicator === "none") return provider?.components?.length ? "当前展示的官方 API 组件正常。" : "当前没有官方活跃事件。";
  const activeBody = safeDisplayText(provider?.active_incidents?.[0]?.body);
  if (activeBody) return activeBody;
  return safeDisplayText(provider?.status_label, "状态未知");
}

function officialSourceNote(provider) {
  if (provider?.id === "gemini") return "Google 官方状态源没有提供可拆分的 Gemini 组件列表，这里只展示 Gemini / Vertex AI 相关事件。";
  if (provider?.id === "deepseek") return "DeepSeek 官方状态页不是标准 Statuspage API；如果服务器访问被对方重置，会显示连接受限并保留官方入口供核对。";
  return "该官方状态源暂未提供可拆分组件列表。";
}

function relativeTime(value) {
  if (!value) return "刚刚更新";
  const ts = Date.parse(value);
  if (!Number.isFinite(ts)) return value;
  const seconds = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (seconds < 60) return `${seconds || 1}秒前`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}小时前`;
  return new Date(ts).toLocaleString("zh-CN", { hour12: false });
}

function formatStatusTime(value) {
  if (!value) return "";
  const ts = Date.parse(value);
  if (!Number.isFinite(ts)) return value;
  return new Date(ts).toLocaleString("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatStatusDateTime(value) {
  if (!value) return "";
  const ts = Date.parse(value);
  if (!Number.isFinite(ts)) return value;
  return new Date(ts).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatStatusDate(value) {
  if (!value) return "";
  const ts = Date.parse(value);
  if (!Number.isFinite(ts)) return value;
  return new Date(ts).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function uptimeBarTitle(day) {
  return `${formatStatusDate(day.date)} · ${day.status === "none" ? "正常" : day.status === "minor" ? "降级" : day.status === "maintenance" ? "维护" : day.status === "major" ? "中断" : "未知"}`;
}

function OfficialStatusStrip({ status, onOpen }) {
  const providers = status?.providers || [];
  if (!providers.length) return null;
  return (
    <section className="official-strip" aria-label="官方 API 状态">
      <button type="button" className="official-strip-title" onClick={() => onOpen()}>
        <Activity size={16} />
        官方 API 状态
      </button>
      <div className="official-strip-items">
        {providers.map((provider) => (
          <button key={provider.id} type="button" className="official-pill" onClick={() => onOpen(provider.id)} title={`${provider.name}: ${provider.description || provider.status_label}`}>
            <span className={`status-dot ${statusTone(provider.indicator)}`} />
            <strong>{provider.name}</strong>
            <span>{officialStatusText(provider)}</span>
          </button>
        ))}
      </div>
      <button type="button" className="official-strip-time" onClick={() => onOpen()}>
        <History size={15} />
        {relativeTime(status.generated_at)}
      </button>
    </section>
  );
}

function OfficialUptimePanel({ provider }) {
  const uptime = provider?.uptime;
  const rows = uptime?.rows || [];
  if (!rows.length) return null;
  return (
    <section className="official-uptime" aria-label="近 90 天可用性">
      <div className="official-uptime-head">
        <div>
          <h3>近 {uptime.window_days || 90} 天可用性</h3>
          <span>{formatStatusDate(uptime.start_date)} - {formatStatusDate(uptime.end_date)}</span>
        </div>
      </div>
      <div className="official-uptime-rows">
        {rows.map((row) => (
          <article key={`${provider.id}-${row.name}`} className="official-uptime-row">
            <div className="official-uptime-row-head">
              <strong>{row.name}</strong>
              <span>{Number.isFinite(row.uptime_percent) ? `${row.uptime_percent.toFixed(2)}% 可用性` : row.status_label}</span>
            </div>
            <div className="official-uptime-bars">
              {(row.daily || []).map((day) => (
                <span key={`${row.name}-${day.date}`} className={`uptime-day ${statusTone(day.status)}`} title={uptimeBarTitle(day)} />
              ))}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function Sidebar({ view, filters, updateFilter, resetFilters, filterMeta, aiNews, aiNewsCategory, setAiNewsCategory }) {
  const isAbout = view === "about";
  const isDetect = view === "detect";
  const isStatus = view === "status";
  const isNews = view === "news";
  const isChat = view === "chat";
  function scrollToAnchor(event, hash) {
    event.preventDefault();
    const target = document.querySelector(hash);
    if (!target) return;
    const topbarHeight = document.querySelector(".topbar")?.getBoundingClientRect().height || 0;
    const y = target.getBoundingClientRect().top + window.scrollY - topbarHeight - 14;
    window.history.replaceState(null, "", hash);
    window.scrollTo({ top: Math.max(0, y), behavior: "smooth" });
  }
  return (
    <aside className="sidebar">
      <div className="filter-head">
        <div>{isAbout ? <Info size={16} /> : isDetect ? <TestTubeDiagonal size={16} /> : isStatus ? <Activity size={16} /> : isNews ? <Newspaper size={16} /> : isChat ? <MessageSquare size={16} /> : <Filter size={16} />}{isAbout ? "目录" : isDetect ? "检测" : isStatus ? "官方状态" : isNews ? "资讯" : isChat ? "对话" : "筛选"}</div>
        {!isAbout && !isDetect && !isStatus && !isNews && !isChat && <button type="button" onClick={resetFilters}><RotateCcw size={14} />重置</button>}
      </div>
      <div className="filters">
        {view === "sites" && (
          <>
            <Select label="状态" value={filters.status} onChange={(v) => updateFilter("status", v)}
              options={[
                ["all", "全部状态"],
                ["online", "在线"],
                ["partial", "部分可读"],
                ["unknown", "未采集"],
              ]}
            />
            <MetaSelect label="供应商" value={filters.provider} onChange={(v) => updateFilter("provider", v)} items={filterMeta?.providers} allLabel="全部供应商" />
            <MetaSelect label="可用令牌分组" value={filters.group} onChange={(v) => updateFilter("group", v)} items={filterMeta?.groups} allLabel="全部分组" />
            <MetaSelect label="计费类型" value={filters.billing} onChange={(v) => updateFilter("billing", v)} items={filterMeta?.billing_types} allLabel="全部类型" />
            <TextFilter label="指定模型" value={filters.model} onChange={(v) => updateFilter("model", v)} placeholder="claude / gpt / deepseek" />
            <Select label="排序" value={activeSiteSort(filters.sort)} onChange={(v) => updateFilter("sort", v)}
              options={[
                ["random", "随机展示"],
                ["online", "在线优先"],
                ["price", "价格低优先"],
                ["models", "模型多优先"],
                ["name", "名称 A-Z"],
              ]}
            />
          </>
        )}
        {view === "models" && (
          <>
            <MetaSelect label="供应商" value={filters.provider} onChange={(v) => updateFilter("provider", v)} items={filterMeta?.model_providers} allLabel="全部供应商" />
            <TextFilter label="模型名称" value={filters.model} onChange={(v) => updateFilter("model", v)} placeholder="gpt-5 / claude / deepseek" />
            <NumberFilter label="最低成功率 %" value={filters.minSuccess} onChange={(v) => updateFilter("minSuccess", v)} placeholder="如 90" />
            <NumberFilter label="最高延迟 秒" value={filters.maxLatency} onChange={(v) => updateFilter("maxLatency", v)} placeholder="如 30" />
            <NumberFilter label="最低 TPS" value={filters.minTps} onChange={(v) => updateFilter("minTps", v)} placeholder="如 30" />
            <Select label="排序" value={activeModelSort(filters.sort)} onChange={(v) => updateFilter("sort", v)}
              options={[
                ["usd", "美元单位站点排序"],
                ["cny", "人民币单位站点排序"],
                ["request", "按次收费站点排序"],
              ]}
            />
          </>
        )}
        {view === "announcements" && (
          <>
            <MetaSelect label="公告标签" value={filters.tag} onChange={(v) => updateFilter("tag", v)} items={filterMeta?.announcement_tags} allLabel="全部公告标签" />
          </>
        )}
        {view === "about" && (
          <div className="about-side">
            <a href="#about-capabilities" onClick={(event) => scrollToAnchor(event, "#about-capabilities")}>页面能力</a>
            <a href="#about-data" onClick={(event) => scrollToAnchor(event, "#about-data")}>数据来源</a>
            <a href="#about-pricing" onClick={(event) => scrollToAnchor(event, "#about-pricing")}>比价口径</a>
            <a href="#about-refresh" onClick={(event) => scrollToAnchor(event, "#about-refresh")}>更新机制</a>
            <a href="#about-boundary" onClick={(event) => scrollToAnchor(event, "#about-boundary")}>使用边界</a>
            <a href="#about-disclaimer" onClick={(event) => scrollToAnchor(event, "#about-disclaimer")}>免责声明</a>
            <a href="#about-feedback" onClick={(event) => scrollToAnchor(event, "#about-feedback")}>问题反馈</a>
          </div>
        )}
        {view === "detect" && (
          <div className="about-side">
            <a href="#detect-form" onClick={(event) => scrollToAnchor(event, "#detect-form")}>检测参数</a>
            <a href="#detect-result" onClick={(event) => scrollToAnchor(event, "#detect-result")}>检测结果</a>
            <a href="#detect-notes" onClick={(event) => scrollToAnchor(event, "#detect-notes")}>注意事项</a>
          </div>
        )}
        {view === "status" && (
          <div className="about-side">
            <a href="#official-overview" onClick={(event) => scrollToAnchor(event, "#official-overview")}>厂商状态</a>
            <a href="#official-history" onClick={(event) => scrollToAnchor(event, "#official-history")}>历史事件</a>
            <a href="#official-notes" onClick={(event) => scrollToAnchor(event, "#official-notes")}>数据来源</a>
          </div>
        )}
        {view === "chat" && (
          <div className="about-side">
            <a href="#chat-config" onClick={(event) => scrollToAnchor(event, "#chat-config")}>连接配置</a>
            <a href="#chat-panel" onClick={(event) => scrollToAnchor(event, "#chat-panel")}>对话窗口</a>
          </div>
        )}
        {view === "news" && (
          <div className="about-side">
            <button type="button" className={aiNewsCategory === "all" ? "active" : ""} onClick={() => setAiNewsCategory?.("all")}>
              全部文章
              <span>{aiNews?.items?.length || 0}</span>
            </button>
            {(aiNews?.categories || []).map((item) => (
              <button key={item.value} type="button" className={aiNewsCategory === item.value ? "active" : ""} onClick={() => setAiNewsCategory?.(item.value)}>
                {item.value}
                <span>{item.count}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

function Select({ label, value, onChange, options, title }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} title={title} onChange={(event) => onChange(event.target.value)}>
        {options.map(([optionValue, optionLabel, optionTitle]) => (
          <option key={optionValue} value={optionValue} title={optionTitle}>{optionLabel}</option>
        ))}
      </select>
    </label>
  );
}

function MetaSelect({ label, value, onChange, items, allLabel }) {
  const options = useMemo(() => [["all", allLabel], ...((items || []).map((item) => [item.value, `${displayProvider(item.value)} · ${item.count}`]))], [items, allLabel]);
  return <Select label={label} value={value} onChange={onChange} options={options} />;
}

function TextFilter({ label, value, onChange, placeholder }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </label>
  );
}

function NumberFilter({ label, value, onChange, placeholder }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" inputMode="decimal" min="0" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </label>
  );
}

function Toolbar({ view, layout, setLayout, data, loading, sortHint }) {
  const title = view === "sites" ? "可用中转站" : view === "models" ? "模型价格索引" : view === "detect" ? "模型检测" : view === "status" ? "官方 API 状态" : view === "news" ? "AI 热点资讯" : view === "announcements" ? "中转站公告" : "关于本站";
  const subtitle =
    view === "sites"
      ? "温馨提示：站点聚合页每 20 分钟随机展示一批在线站点。"
      : view === "models"
        ? "按模型横向比较各站点价格"
        : view === "detect"
          ? "用自己的 API Key 对指定站点和模型做实时请求检测"
          : view === "status"
            ? "汇总主流模型厂商官方状态页"
            : view === "news"
        ? "聚合官方动态、模型发布通知与实用教程"
              : view === "announcements"
                ? "跟踪模型上架、价格调整、维护与活动"
                : "了解 RelayWatch 的数据来源、更新方式与比价边界";
  return (
    <div className="toolbar">
      <div>
        <span className="eyebrow">{view === "announcements" ? "公告流" : view === "models" ? "价格索引" : view === "detect" ? "模型检测" : view === "status" ? "官方状态" : view === "news" ? "热点资讯" : view === "about" ? "关于本站" : "站点目录"}</span>
        <h2>{title}</h2>
        <p>{subtitle}</p>
        {sortHint && (
          <div className="toolbar-hint">
            {(Array.isArray(sortHint) ? sortHint : [sortHint]).map((line, index) => (
              <p key={line}>
                <span aria-hidden="true">💡</span>
                <span>{line}</span>
              </p>
            ))}
          </div>
        )}
      </div>
      <div className="toolbar-side">
        {view !== "about" && view !== "detect" && view !== "status" && view !== "news" && <span className="result-count">{loading ? "加载中..." : `共 ${data.total || 0} 条，当前第 ${data.page || 1} 页`}</span>}
        {view === "sites" && (
          <div className="segmented" aria-label="视图切换">
            <button title="卡片视图" className={layout === "grid" ? "active" : ""} onClick={() => setLayout("grid")} type="button"><LayoutGrid size={16} /></button>
            <button title="列表视图" className={layout === "list" ? "active" : ""} onClick={() => setLayout("list")} type="button"><List size={16} /></button>
          </div>
        )}
      </div>
    </div>
  );
}

function Content({ view, layout, data, loading, openSite, openPriceDrawer, modelSort, filters, summary, officialStatus, aiNews, newsQuery, reloadAiNews, aiNewsCategory, setAiNewsCategory, officialProviderId, setOfficialProviderId, reloadOfficialStatus }) {
  if (view === "about") return <AboutPage summary={summary} />;
  if (view === "tools") return <ToolsPage />;
  if (view === "detect") return <ToolsPage initialTool="detect" />;
  if (view === "status") return <OfficialStatusPage status={officialStatus} selectedProviderId={officialProviderId} setSelectedProviderId={setOfficialProviderId} reload={reloadOfficialStatus} />;
  if (view === "chat") return <ToolsPage initialTool="chat" />;
  if (view === "news") return <AiNewsPage news={aiNews} query={newsQuery} reload={reloadAiNews} category={aiNewsCategory} setCategory={setAiNewsCategory} />;
  const hasItems = Boolean(data.items?.length);
  if (loading && !hasItems) return <div className="empty"><Loader2 className="spin" size={22} />加载中，稍慢时会自动超时提示</div>;
  if (data.error) return <div className="empty error">{data.errorMessage || "加载失败，请刷新重试"}</div>;
  if (!hasItems) return <div className="empty">没有匹配结果</div>;
  const loadingBadge = loading ? (
    <div className="content-loading-badge"><Loader2 className="spin" size={14} />更新中</div>
  ) : null;
  if (view === "sites") return <section className={`content-frame ${loading ? "refreshing" : ""}`} aria-busy={loading}>{loadingBadge}<div className={`site-grid ${layout}`}>{data.items.map((site) => <SiteCard key={site.id} site={site} layout={layout} openSite={openSite} />)}</div></section>;
  if (view === "models") return <section className={`content-frame ${loading ? "refreshing" : ""}`} aria-busy={loading}>{loadingBadge}<div className="rows">{data.items.map((model, index) => <ModelRow key={`${model.provider}::${model.model}::${index}`} model={model} modelSort={modelSort} filters={filters} openSite={openSite} openPriceDrawer={openPriceDrawer} />)}</div></section>;
  return <section className={`content-frame ${loading ? "refreshing" : ""}`} aria-busy={loading}>{loadingBadge}<div className="timeline">{data.items.map((item) => <Announcement key={item.id} item={item} />)}</div></section>;
}

const CHAT_SETTINGS_KEY = "relaywatch-chat-settings";

function ToolsPage({ initialTool = "detect" }) {
  const [activeTool, setActiveTool] = useState(initialTool);
  useEffect(() => {
    setActiveTool(initialTool);
  }, [initialTool]);
  return (
    <section className="tools-page">
      <div className="tools-head">
        <div>
          <span className="eyebrow">Site Tools</span>
          <h2>站点工具</h2>
          <p>把在线对话和模型检测放到同一个工作台里，先试接口，再做检测。</p>
        </div>
        <div className="tools-tabs" aria-label="站点工具切换">
          <button type="button" className={activeTool === "detect" ? "active" : ""} onClick={() => setActiveTool("detect")}>
            <TestTubeDiagonal size={16} />
            模型检测
          </button>
          <button type="button" className={activeTool === "chat" ? "active" : ""} onClick={() => setActiveTool("chat")}>
            <MessageSquare size={16} />
            在线对话
          </button>
        </div>
      </div>
      {activeTool === "chat" ? <ChatPage /> : <DetectionPage />}
    </section>
  );
}

function readChatSettings() {
  try {
    return JSON.parse(window.localStorage.getItem(CHAT_SETTINGS_KEY) || "{}");
  } catch {
    return {};
  }
}

const CODE_KEYWORDS = new Set([
  "and", "as", "async", "await", "break", "case", "catch", "class", "const", "continue", "def", "default", "del", "do",
  "elif", "else", "except", "export", "finally", "for", "from", "function", "if", "import", "in", "let", "new", "not",
  "or", "pass", "return", "switch", "try", "var", "while", "with", "yield", "True", "False", "None", "true", "false", "null",
]);

function highlightCode(code) {
  const pattern = /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\/\/[^\n]*|#[^\n]*|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][A-Za-z0-9_]*(?=\s*\()|\b[A-Za-z_][A-Za-z0-9_]*\b)/g;
  const nodes = [];
  let lastIndex = 0;
  let index = 0;
  for (const match of code.matchAll(pattern)) {
    const text = match[0];
    if (match.index > lastIndex) nodes.push(code.slice(lastIndex, match.index));
    let tokenClass = "";
    if (text.startsWith("//") || text.startsWith("#")) tokenClass = "tok-comment";
    else if (text.startsWith("\"") || text.startsWith("'") || text.startsWith("`")) tokenClass = "tok-string";
    else if (/^\d/.test(text)) tokenClass = "tok-number";
    else if (CODE_KEYWORDS.has(text)) tokenClass = "tok-keyword";
    else if (/\w/.test(text)) tokenClass = "tok-function";
    nodes.push(tokenClass ? <span className={tokenClass} key={`tok-${index++}`}>{text}</span> : text);
    lastIndex = match.index + text.length;
  }
  if (lastIndex < code.length) nodes.push(code.slice(lastIndex));
  return nodes;
}

function ChatCodeBlock({ className = "", children }) {
  const [copied, setCopied] = useState(false);
  const code = String(children || "").replace(/\n$/, "");
  const match = /language-([\w-]+)/.exec(className || "");
  const language = match?.[1] || "text";
  const isInline = !match && !code.includes("\n");
  if (isInline) return <code>{children}</code>;
  async function copyCode() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1300);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="chat-code-block">
      <div className="chat-code-head">
        <span>{language}</span>
        <button type="button" onClick={copyCode}>
          {copied ? <CheckCircle2 size={14} /> : <Clipboard size={14} />}
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre><code className={className}>{highlightCode(code)}</code></pre>
    </div>
  );
}

function ChatPage() {
  const saved = useMemo(readChatSettings, []);
  const [baseUrl, setBaseUrl] = useState(saved.baseUrl || "");
  const [apiKey, setApiKey] = useState(saved.apiKey || "");
  const [model, setModel] = useState(saved.model || PREFERRED_CHAT_MODEL);
  const [temperature, setTemperature] = useState(saved.temperature || "0.7");
  const [maxTokens, setMaxTokens] = useState(saved.maxTokens || "2048");
  const [models, setModels] = useState(saved.models || []);
  const [loadingModels, setLoadingModels] = useState(false);
  const [remember, setRemember] = useState(Boolean(saved.remember));
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("");
  const [sending, setSending] = useState(false);
  const [messages, setMessages] = useState([
    { id: "welcome", role: "assistant", content: "准备好后开始对话。" },
  ]);
  const abortRef = useRef(null);
  const messagesRef = useRef(null);
  const autoScrollRef = useRef(true);

  useEffect(() => {
    if (!remember) {
      window.localStorage.removeItem(CHAT_SETTINGS_KEY);
      return;
    }
    window.localStorage.setItem(CHAT_SETTINGS_KEY, JSON.stringify({ baseUrl, apiKey, model, temperature, maxTokens, models, remember }));
  }, [baseUrl, apiKey, model, temperature, maxTokens, models, remember]);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el || !autoScrollRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  function handleChatScroll(event) {
    const el = event.currentTarget;
    autoScrollRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
  }

  function stopChat() {
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    setStatus("已停止生成");
  }

  function clearChat() {
    stopChat();
    setMessages([{ id: "welcome", role: "assistant", content: "对话已清空。" }]);
    setStatus("");
  }

  async function loadModels() {
    if (!baseUrl.trim() || !apiKey.trim()) {
      setStatus("请先填写 API 地址和 API Key");
      return;
    }
    setLoadingModels(true);
    setStatus("正在获取模型");
    try {
      const result = await postApi("/api/chat/models", { base_url: baseUrl, api_key: apiKey });
      const nextModels = result.items || [];
      setModels(nextModels);
      if (nextModels.length && (!model || !nextModels.includes(model))) setModel(preferredModelFromList(nextModels));
      setStatus(nextModels.length ? `已获取 ${nextModels.length} 个模型` : "没有获取到模型");
    } catch (error) {
      setStatus(messageFromUnknown(error, "模型获取失败"));
    } finally {
      setLoadingModels(false);
    }
  }

  async function sendChat() {
    const text = input.trim();
    if (!text || sending) return;
    if (!baseUrl.trim() || !apiKey.trim() || !model.trim()) {
      setStatus("请先填写 API 地址、API Key 和模型名称");
      return;
    }
    const userMessage = { id: `u-${Date.now()}`, role: "user", content: text };
    const assistantId = `a-${Date.now()}`;
    const nextMessages = [...messages.filter((item) => item.id !== "welcome"), userMessage];
    setMessages([...nextMessages, { id: assistantId, role: "assistant", content: "" }]);
    setInput("");
    setSending(true);
    setStatus("正在连接接口");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await fetch("/api/chat/proxy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_url: baseUrl,
          api_key: apiKey,
          model,
          temperature,
          max_tokens: maxTokens,
          stream: true,
          messages: nextMessages.map(({ role, content }) => ({ role, content })),
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(messageFromUnknown(data.detail || data, `${response.status} ${response.statusText}`));
      }
      const reader = response.body?.getReader();
      if (!reader) throw new Error("浏览器不支持流式读取");
      const decoder = new TextDecoder();
      let pending = "";
      setStatus("正在生成");
      while (true) {
        const { value, done } = await reader.read();
        pending += decoder.decode(value || new Uint8Array(), { stream: !done });
        const parts = pending.split("\n\n");
        pending = parts.pop() || "";
        for (const part of parts) {
          const line = part.split("\n").find((item) => item.startsWith("data:"));
          if (!line) continue;
          try {
            const payload = JSON.parse(line.slice(5).trim());
            if (payload.error) throw new Error(messageFromUnknown(payload.error, "对话请求失败"));
            if (payload.text) {
              setMessages((current) => current.map((item) => item.id === assistantId ? { ...item, content: `${item.content}${payload.text}` } : item));
            }
          } catch (error) {
            throw new Error(messageFromUnknown(error, "对话请求失败"));
          }
        }
        if (done) break;
      }
      setStatus("生成完成");
    } catch (error) {
      if (error.name === "AbortError") return;
      const text = messageFromUnknown(error, "对话请求失败");
      setStatus("请求失败");
      setMessages((current) => current.map((item) => item.id === assistantId && !item.content ? { ...item, content: `请求失败：${text}` } : item));
    } finally {
      abortRef.current = null;
      setSending(false);
    }
  }

  return (
    <section className="chat-page">
      <section className="chat-config" id="chat-config">
        <div className="chat-section-head">
          <h3>在线对话</h3>
          <span>{status || "OpenAI 兼容接口"}</span>
        </div>
        <div className="chat-config-fields">
          <label className="field">
            <span>API 地址</span>
            <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1" />
          </label>
          <label className="field">
            <span>API Key</span>
            <input value={apiKey} onChange={(event) => setApiKey(event.target.value)} type="password" placeholder="sk-..." />
          </label>
          <label className="field">
            <span>模型</span>
            <select value={model} onChange={(event) => setModel(event.target.value)}>
              {model && !models.includes(model) && <option value={model}>{model}</option>}
              {!models.length && <option value={model}>{model || "请先获取模型"}</option>}
              {models.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <button type="button" className="chat-model-button" onClick={loadModels} disabled={loadingModels}>
            {loadingModels ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
            获取模型
          </button>
        </div>
        <details className="chat-advanced">
          <summary>高级设置</summary>
          <div className="chat-config-grid">
            <label className="field">
              <span>温度</span>
              <input value={temperature} onChange={(event) => setTemperature(event.target.value)} inputMode="decimal" />
            </label>
            <label className="field">
              <span>最大输出</span>
              <input value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} inputMode="numeric" />
            </label>
          </div>
        </details>
        <label className="chat-check">
          <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
          <span>记住配置</span>
        </label>
        <div className="chat-config-actions">
          <button type="button" onClick={clearChat}>清空对话</button>
          {sending && <button type="button" onClick={stopChat}>停止生成</button>}
        </div>
      </section>
      <section className="chat-panel" id="chat-panel">
        <div className="chat-panel-head">
          <div>
            <h3>对话窗口</h3>
            <span>{status || "等待输入"}</span>
          </div>
          <span>{model || PREFERRED_CHAT_MODEL}</span>
        </div>
        <div className="chat-messages" ref={messagesRef} onScroll={handleChatScroll}>
          {messages.map((message) => (
            <article key={message.id} className={`chat-message ${message.role}`}>
              <div>{message.role === "user" ? "你" : "AI"}</div>
              <div className="chat-bubble">
                {message.content ? (
                  <ReactMarkdown components={{
                    pre: ({ children }) => <>{children}</>,
                    code: ChatCodeBlock,
                  }}>{message.content}</ReactMarkdown>
                ) : <Loader2 className="spin" size={16} />}
              </div>
            </article>
          ))}
        </div>
        <div className="chat-input-row">
          <textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              sendChat();
            }
          }} placeholder="输入消息" />
          <button type="button" onClick={sendChat} disabled={sending || !input.trim()}>
            {sending ? <Loader2 className="spin" size={16} /> : <Send size={16} />}
            发送
          </button>
        </div>
      </section>
    </section>
  );
}

function AiNewsPage({ news, query = "", reload, category = "all", setCategory }) {
  const [refreshing, setRefreshing] = useState(false);
  const [activeArticle, setActiveArticle] = useState(null);
  const [articleLoadingId, setArticleLoadingId] = useState("");
  const [newsPage, setNewsPage] = useState(1);
  const newsListRef = useRef(null);
  const items = news?.items || [];
  const tutorials = news?.tutorials || [];
  const allItems = useMemo(() => {
    const seen = new Set(items.map((item) => item.id));
    const extraTutorials = tutorials
      .filter((item) => item?.id && !seen.has(item.id))
      .map((item) => ({
        ...item,
        kind: item.kind || "tutorial",
        category: item.category || "教程",
        source: item.source || item.provider || "教程",
        provider: item.provider || item.source || "教程",
      }));
    return [...items, ...extraTutorials];
  }, [items, tutorials]);
  const categories = useMemo(() => {
    const counts = new Map();
    allItems.forEach((item) => {
      const key = item.category || "动态";
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return Array.from(counts.entries())
      .map(([value, count]) => ({ value, count }))
      .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value, "zh-Hans-CN"));
  }, [allItems]);
  const newsPageSize = 10;
  const q = (query || "").trim().toLowerCase();
  const categoryItems = category === "all" ? allItems : allItems.filter((item) => item.category === category);
  const filtered = !q
    ? categoryItems
    : categoryItems.filter((item) => [
        item.title,
        item.title_zh,
        item.summary,
        item.provider,
        item.source,
        item.category,
      ].join(" ").toLowerCase().includes(q));
  const newsPages = Math.max(1, Math.ceil(filtered.length / newsPageSize));
  const safeNewsPage = Math.min(Math.max(1, newsPage), newsPages);
  const pagedItems = filtered.slice((safeNewsPage - 1) * newsPageSize, safeNewsPage * newsPageSize);
  const officialCount = allItems.filter((item) => item.kind === "official").length;
  const tutorialCount = tutorials.length;
  const visibleSources = useMemo(() => {
    const counts = new Map();
    filtered.forEach((item) => {
      const name = normalizedNewsSourceName(item.source || item.provider);
      if (!name || name === "Telegram") return;
      counts.set(name, (counts.get(name) || 0) + 1);
    });
    return Array.from(counts.entries())
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh-Hans-CN"))
      .slice(0, 4);
  }, [filtered]);
  const hiddenSourceCount = Math.max(0, new Set(filtered.map((item) => normalizedNewsSourceName(item.source || item.provider)).filter((name) => name && name !== "Telegram")).size - visibleSources.length);
  const chooseCategory = (nextCategory) => setCategory?.(nextCategory);

  useEffect(() => {
    setNewsPage(1);
  }, [category, q]);

  useEffect(() => {
    if (newsPage > newsPages) setNewsPage(newsPages);
  }, [newsPage, newsPages]);

  useEffect(() => {
    if (category === "all" || !news) return;
    if (!categories.some((item) => item.value === category)) {
      setCategory?.("all");
    }
  }, [category, categories, news, setCategory]);

  async function refresh() {
    if (!reload) return;
    setRefreshing(true);
    try {
      await reload();
    } finally {
      setRefreshing(false);
    }
  }

  async function openArticle(item) {
    if (!item) return;
    if (item.content || item.content_html) {
      setActiveArticle(item);
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    setArticleLoadingId(item.id || "");
    try {
      const full = await api(`/api/ai-news/articles/${encodeURIComponent(item.id)}`);
      setActiveArticle({ ...item, ...full });
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch {
      setActiveArticle(item);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } finally {
      setArticleLoadingId("");
    }
  }

  function scrollToNewsList() {
    window.requestAnimationFrame(() => {
      const target = newsListRef.current || document.getElementById("ai-news-feed");
      if (!target) return;
      const topbarHeight = document.querySelector(".topbar")?.getBoundingClientRect().height || 0;
      const y = target.getBoundingClientRect().top + window.scrollY - topbarHeight - 14;
      window.scrollTo({ top: Math.max(0, y), behavior: "smooth" });
    });
  }

  function goNewsPage(nextPage) {
    setNewsPage(Math.min(newsPages, Math.max(1, nextPage)));
    scrollToNewsList();
  }

  if (!news) {
    return <div className="empty"><Loader2 className="spin" size={22} />正在整理 AI 资讯</div>;
  }

  if (activeArticle) {
    return <AiArticlePage article={activeArticle} articles={allItems} tutorials={tutorials} onSelectArticle={openArticle} onBack={() => setActiveArticle(null)} />;
  }

  return (
    <section className="ai-news-page" id="ai-news-top">
      <div className="ai-news-hero">
        <div>
          <span className="eyebrow">AI intelligence</span>
          <h3>AI 热点文章与模型动态</h3>
          <p>聚合 AI 资讯、模型发布、研究观察和开发者文章；点开卡片即可在站内阅读正文，并可跳转原文核对来源。</p>
        </div>
        <div className="ai-news-stats">
          <Stat label="文章" value={allItems.length} />
          <Stat label="来源" value={visibleSources.length} />
          <Stat label="教程" value={tutorialCount} />
        </div>
      </div>

      <div className="ai-news-tabs" id="ai-news-feed">
        <button type="button" className={category === "all" ? "active" : ""} onClick={() => chooseCategory("all")}>全部</button>
        {categories.map((item) => (
          <button key={item.value} type="button" className={category === item.value ? "active" : ""} onClick={() => chooseCategory(item.value)}>
            {item.value}<span>{item.count}</span>
          </button>
        ))}
        <button className="submit-shortcut compact ai-news-refresh" type="button" onClick={refresh}>
          <RefreshCw className={refreshing ? "spin" : ""} size={16} />
          刷新资讯
        </button>
      </div>

      <div className="ai-news-layout">
        <div className="ai-news-list" ref={newsListRef}>
          {pagedItems.map((item) => <AiNewsCard key={item.id} item={item} onOpen={openArticle} loading={articleLoadingId === item.id} />)}
          {!filtered.length && <div className="empty small">{q ? "没有匹配搜索词的资讯" : "暂无这个分类的资讯"}</div>}
          {filtered.length > newsPageSize && (
            <div className="ai-news-pager">
              <button type="button" disabled={safeNewsPage <= 1} onClick={() => goNewsPage(safeNewsPage - 1)}>
                <ChevronLeft size={15} />上一页
              </button>
              <span>{safeNewsPage} / {newsPages} · 共 {filtered.length} 篇</span>
              <button type="button" disabled={safeNewsPage >= newsPages} onClick={() => goNewsPage(safeNewsPage + 1)}>
                下一页<ChevronRight size={15} />
              </button>
            </div>
          )}
        </div>
        <aside className="ai-news-side">
          <section id="ai-news-tutorials">
            <h3><BookOpen size={16} />实用教程</h3>
            {tutorials.map((item) => <AiTutorialCard key={item.id} item={item} onOpen={openArticle} loading={articleLoadingId === item.id} />)}
          </section>
          <section id="ai-news-sources">
            <h3><Database size={16} />主要来源</h3>
            {visibleSources.map((item) => (
              <div className="source-state ok" key={item.name}>
                <span className="status-dot ok" />
                <div>
                  <strong>{item.name}</strong>
                  <span>{item.count} 篇正在展示</span>
                </div>
              </div>
            ))}
            {hiddenSourceCount > 0 && (
              <div className="source-state muted">
                <span className="status-dot unknown" />
                <div>
                  <strong>其他来源</strong>
                  <span>另有 {hiddenSourceCount} 个来源</span>
                </div>
              </div>
            )}
          </section>
        </aside>
      </div>
    </section>
  );
}

function AiNewsCard({ item, featured = false, onOpen, loading = false }) {
  const hasContent = Boolean((item.content || "").trim());
  const isCommunity = item.kind === "community" || item.category === "社区讨论" || item.category === "技术社区";
  const canOpenSource = Boolean(item.url && !/^https?:\/\/(?:t\.me|telegram\.me)\//i.test(item.url));
  const content = (
    <>
      <div className="ai-card-top">
        <span>{item.category || "动态"}</span>
        <span>{item.provider || item.source}</span>
      </div>
      <h3>{safeDisplayText(item.title_zh, item.title || "AI 动态")}</h3>
      <p>{safeDisplayText(item.summary, "打开来源查看完整内容。")}</p>
      <div className="ai-card-foot">
        <span>{item.published_at ? formatStatusDateTime(item.published_at) : item.source}</span>
        <span>{loading ? "加载正文..." : isCommunity && canOpenSource ? "打开原帖" : hasContent ? "阅读全文" : "查看详情"}</span>
      </div>
    </>
  );
  const className = `ai-news-card ${featured ? "featured" : ""}`;
  if (isCommunity && canOpenSource) {
    return <a className={className} href={item.url} target="_blank" rel="noreferrer">{content}</a>;
  }
  return <button type="button" className={className} onClick={() => onOpen?.(item)} disabled={loading}>{content}</button>;
}

function AiArticlePage({ article, articles, tutorials, onSelectArticle, onBack }) {
  const related = (articles || []).filter((item) => item.id !== article.id && (item.category === article.category || item.provider === article.provider)).slice(0, 4);
  const articleContent = (article.content || article.excerpt || "").trim();
  const articleHtml = (article.content_html || "").trim();
  const bulletItems = Array.isArray(article.bullets) ? article.bullets.filter(Boolean) : [];
  const hasFullContent = Boolean((article.content || "").trim());
  const hasReadableFallback = Boolean(article.summary || bulletItems.length);
  const canOpenSource = Boolean(article.url && !/^https?:\/\/(?:t\.me|telegram\.me)\//i.test(article.url));
  return (
    <section className="ai-article-page">
      <button type="button" className="article-back" onClick={onBack}><ChevronLeft size={16} />返回资讯列表</button>
      <div className="ai-article-layout">
        <article className="ai-article-main">
          <header className="ai-article-header">
            <div className="ai-card-top">
              <span>{article.category || "AI 资讯"}</span>
              <span>{article.provider || article.source}</span>
            </div>
            <h1>{safeDisplayText(article.title_zh, article.title || "AI 资讯")}</h1>
            <p>{article.source} · {article.published_at ? formatStatusDateTime(article.published_at) : "来源入口"}</p>
            {canOpenSource && (
              <a className="ai-original-link" href={article.url} target="_blank" rel="noreferrer">
                原文地址 <ExternalLink size={15} />
              </a>
            )}
          </header>
          {article.title && article.title !== article.title_zh && <p className="ai-article-original">原文标题：{article.title}</p>}
          <section className="ai-article-body">
            {articleHtml ? (
              <div className="ai-article-html" dangerouslySetInnerHTML={{ __html: articleHtml }} />
            ) : !articleContent && hasReadableFallback ? (
              <div className="ai-article-fallback">
                {article.summary && <p>{article.summary}</p>}
                {!!bulletItems.length && (
                  <ul>
                    {bulletItems.map((bullet) => <li key={bullet}>{bullet}</li>)}
                  </ul>
                )}
              </div>
            ) : (
              <p>{safeDisplayText(articleContent, "该来源暂时没有提取到正文，请打开原文阅读。")}</p>
            )}
          </section>
          <footer className="ai-article-footer">
            <span>{hasFullContent ? `正文来自 ${article.source || article.provider || "授权来源"}，已保留原文地址。` : hasReadableFallback ? "当前展示的是教程摘要和检查要点。" : "该来源暂时没有提取到正文，可打开原文继续阅读。"}</span>
            {canOpenSource && <a className="submit-shortcut compact" href={article.url} target="_blank" rel="noreferrer">打开原文 <ExternalLink size={14} /></a>}
          </footer>
        </article>
        <aside className="ai-article-side">
          <section>
            <h3><Newspaper size={16} />文章来源</h3>
            <div className="source-state ok">
              <span className="status-dot ok" />
              <div>
                <strong>{article.source}</strong>
                <span>{article.provider || "AI 资讯源"}</span>
              </div>
            </div>
          </section>
          {!!related.length && (
            <section>
              <h3><Rocket size={16} />相关文章</h3>
              {related.map((item) => (
                <button type="button" className="related-article" key={item.id} onClick={() => { onSelectArticle?.(item); window.scrollTo({ top: 0, behavior: "smooth" }); }}>
                  {safeDisplayText(item.title_zh, item.title || "AI 资讯")}
                </button>
              ))}
            </section>
          )}
          {!!tutorials.length && (
            <section>
              <h3><BookOpen size={16} />实用教程</h3>
              {tutorials.slice(0, 3).map((item) => <AiTutorialCard key={item.id} item={item} onOpen={onSelectArticle} />)}
            </section>
          )}
        </aside>
      </div>
    </section>
  );
}

function AiTutorialCard({ item, onOpen, loading = false }) {
  const content = (
    <>
      <div>
        <span>{item.category || "教程"}</span>
        <span>{item.level || item.provider || "实用"}</span>
      </div>
      <h4>{item.title}</h4>
      <p>{item.summary}</p>
      {!!(item.bullets || []).length && (
        <ul>
          {(item.bullets || []).map((bullet) => <li key={bullet}>{bullet}</li>)}
        </ul>
      )}
      {item.url && <small>{loading ? "正在加载正文..." : `${item.source || item.provider} · 原文地址已保留`}</small>}
    </>
  );
  if (onOpen) {
    return <button type="button" className="ai-tutorial-card clickable" onClick={() => onOpen(item)} disabled={loading}>{content}</button>;
  }
  return (
    <article className="ai-tutorial-card">
      {content}
    </article>
  );
}

function OfficialStatusPage({ status, selectedProviderId = "openai", setSelectedProviderId, reload }) {
  const [selectedId, setSelectedId] = useState(selectedProviderId || "openai");
  const [refreshing, setRefreshing] = useState(false);
  const providers = status?.providers || [];
  const selected = providers.find((item) => item.id === selectedId) || providers[0];

  useEffect(() => {
    if (!selectedProviderId || selectedProviderId === selectedId) return;
    setSelectedId(selectedProviderId);
  }, [selectedProviderId, selectedId]);

  useEffect(() => {
    if (selected || !providers[0]) return;
    setSelectedId(providers[0].id);
    setSelectedProviderId?.(providers[0].id);
  }, [providers, selected, setSelectedProviderId]);

  function selectProvider(providerId) {
    setSelectedId(providerId);
    setSelectedProviderId?.(providerId);
  }

  async function refresh() {
    if (!reload) return;
    setRefreshing(true);
    try {
      await reload();
    } finally {
      setRefreshing(false);
    }
  }

  if (!providers.length) {
    return <div className="empty"><Loader2 className="spin" size={22} />正在获取官方状态</div>;
  }

  return (
    <section className="official-page" id="official-overview">
      <div className="official-action-row">
        <span>来自各厂商官方状态页，时间统一按中国时间显示。</span>
        <button className="submit-shortcut compact" type="button" onClick={refresh}>
          <RefreshCw className={refreshing ? "spin" : ""} size={16} />
          刷新状态
        </button>
      </div>

      <div className="official-tabs" role="tablist">
        {providers.map((provider) => (
          <button
            key={provider.id}
            type="button"
            role="tab"
            className={provider.id === selected?.id ? "active" : ""}
            onClick={() => selectProvider(provider.id)}
          >
            <span className={`status-dot ${statusTone(provider.indicator)}`} />
            <strong>{provider.name}</strong>
            <span>{provider.subtitle}</span>
          </button>
        ))}
      </div>

      {selected && (
        <>
          <article className={`official-current ${statusTone(selected.indicator)}`}>
            <div>
              <span>当前状态</span>
              <strong>{safeDisplayText(selected.description, selected.status_label || "状态未知")}</strong>
              <p>{providerStatusSummary(selected)}</p>
              {selected.error && <p className="official-error">获取失败：{selected.error}</p>}
            </div>
            <div className="official-current-side">
              <span>监测时间：{formatStatusTime(status.generated_at)}</span>
              <a href={selected.status_url} target="_blank" rel="noreferrer">
                官方状态页 <ExternalLink size={14} />
              </a>
            </div>
          </article>

          {selected.components?.length > 0 && (
            <div className="official-components">
              {selected.components.map((component) => (
                <div key={`${selected.id}-${component.name}`} className="official-component">
                  <span className={`status-dot ${statusTone(component.status)}`} />
                  <strong>{component.name}</strong>
                  <span>{component.status_label}</span>
                </div>
              ))}
            </div>
          )}
          {!selected.components?.length && (
            <div className="official-source-note">
              <Info size={15} />
              <span>{officialSourceNote(selected)}</span>
            </div>
          )}

          <OfficialUptimePanel provider={selected} />

          <div className="official-history-head" id="official-history">
            <h3>历史记录</h3>
            <span>{selected.history?.length || 0}</span>
          </div>
          <div className="official-history">
            {(selected.history || []).map((incident) => (
              <article key={`${selected.id}-${incident.id || incident.name}`} className="official-incident">
                <div className="official-incident-date">
                  <strong>{formatStatusDateTime(incident.created_at || incident.updated_at) || "-"}</strong>
                  <span>{incident.status_label || formatStatusDateTime(incident.updated_at || incident.resolved_at)}</span>
                </div>
                <div className="official-incident-body">
                  <span className={`status-dot ${statusTone(incident.impact)}`} />
                  <div>
                    <h4>{safeDisplayText(incident.name_zh, safeDisplayText(incident.name, "官方事件"))}</h4>
                    <p>{safeDisplayText(incident.body, safeDisplayText(incident.status_label || incident.status, "官方未提供更多描述。"))}</p>
                  </div>
                </div>
              </article>
            ))}
            {!selected.history?.length && <div className="empty small">暂无可展示的历史事件</div>}
          </div>

          <div className="official-note" id="official-notes">
            <Info size={16} />
            <span>OpenAI 与 Claude 使用官方 Statuspage API；Gemini 使用 Google Cloud 状态事件源按关键词筛选；DeepSeek 优先读取官方 RSS/Atom 与状态页，服务器连接受限时会明确标注。未找到公开官方状态页的厂商暂不展示。</span>
          </div>
        </>
      )}
    </section>
  );
}

function AboutPage({ summary }) {
  const [submitUrl, setSubmitUrl] = useState("");
  const [submitState, setSubmitState] = useState({ status: "idle", message: "" });
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackContact, setFeedbackContact] = useState("");
  const [feedbackState, setFeedbackState] = useState({ status: "idle", message: "" });
  const [feedbackItems, setFeedbackItems] = useState([]);
  const [adminToken, setAdminToken] = useState(() => window.sessionStorage.getItem("relaywatch_admin_token") || "");
  const stats = [
    ["候选站点", summary?.sites ?? "-", "按主域与入口去重后的站点快照"],
    ["在线接口", summary?.online_sites ?? "-", "当前可读 status、首页或价格接口的站点"],
    ["模型索引", summary?.models ?? "-", "按规范化模型名聚合后的比价条目"],
    ["公告记录", summary?.announcements ?? "-", "来自 notice 与 status announcements 的独立公告"],
  ];
  const capabilities = [
    [Server, "站点聚合", "把公开中转站按供应商、分组、模型、状态和价格整理到同一张目录里。"],
    [Sparkles, "模型比价", "围绕同一个模型横向比较各站点价格、计费分组和可解析的性能指标。"],
    [Bell, "公告流", "汇总 notice 与系统 announcements，按时间追踪上架、调价、维护和活动。"],
  ];
  const steps = [
    ["发现", "从互联网公开信息中整理候选入口"],
    ["探测", "请求公开接口，识别 status、pricing、notice 等可读数据"],
    ["归一", "清洗站点、模型、分组、价格、公告和性能字段"],
    ["索引", "生成站点聚合、模型比价和公告流三组查询视图"],
    ["切换", "新数据确认可用后再上线，尽量减少访问中断"],
  ];
  async function loadFeedback() {
    try {
      const result = await api("/api/feedback", { limit: 10 });
      setFeedbackItems(result.items || []);
    } catch {
      setFeedbackItems([]);
    }
  }
  useEffect(() => {
    loadFeedback();
  }, []);
  async function submitSite(event) {
    event.preventDefault();
    const value = submitUrl.trim();
    if (!value) {
      setSubmitState({ status: "error", message: "请输入站点地址" });
      return;
    }
    setSubmitState({ status: "loading", message: "正在获取站点内容..." });
    try {
      const result = await postApi("/api/submit-site", { origin: value });
      setSubmitState({
        status: "success",
        message: `${result.message}：${result.origin}`,
      });
      setSubmitUrl("");
    } catch (error) {
      setSubmitState({ status: "error", message: messageFromUnknown(error, "提交失败，请稍后重试") });
    }
  }
  async function submitFeedback(event) {
    event.preventDefault();
    const content = feedbackText.trim();
    if (!content) {
      setFeedbackState({ status: "error", message: "请填写反馈内容" });
      return;
    }
    setFeedbackState({ status: "loading", message: "正在提交反馈..." });
    try {
      const result = await postApi("/api/feedback", {
        type: "feedback",
        content,
        contact: feedbackContact.trim(),
        page_url: window.location.href,
      });
      setFeedbackState({ status: "success", message: `${result.message} 时间：${formatStatusDateTime(result.created_at)}` });
      setFeedbackText("");
      setFeedbackContact("");
      loadFeedback();
    } catch (error) {
      setFeedbackState({ status: "error", message: messageFromUnknown(error, "反馈提交失败，请稍后重试") });
    }
  }
  async function deleteFeedback(item) {
    if (!adminToken.trim()) {
      setFeedbackState({ status: "error", message: "请先填写管理员口令" });
      return;
    }
    if (!item?.id) return;
    window.sessionStorage.setItem("relaywatch_admin_token", adminToken.trim());
    setFeedbackState({ status: "loading", message: "正在处理反馈..." });
    try {
      await deleteApi(`/api/feedback/${item.id}`, adminToken.trim());
      setFeedbackState({ status: "success", message: "反馈已处理" });
      loadFeedback();
    } catch (error) {
      setFeedbackState({ status: "error", message: messageFromUnknown(error, "删除失败") });
    }
  }
  return (
    <section className="about-page">
      <div className="about-hero">
        <div>
          <span className="eyebrow">About RelayWatch</span>
          <h3>把分散的中转站信息整理成能比较、能追踪、能复查的目录。</h3>
          <p>
            RelayWatch 关注公开可访问的中转站接口、模型价格、可用分组和公告动态。它不是排行榜，也不替任何站点背书；它更像一张持续刷新的索引表，帮助你少翻几十个页面，多看几组可验证的数据。
          </p>
          <div className="about-hero-actions" aria-label="页面锚点">
            <a href="#about-data">查看数据来源</a>
            <a href="#about-boundary">了解使用边界</a>
          </div>
        </div>
        <div className="about-stat-grid">
          {stats.map(([label, value, helper]) => (
            <div className="about-stat" key={label}>
              <strong>{value}</strong>
              <span>{label}</span>
              <p>{helper}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="about-feature-strip" id="about-capabilities">
        {capabilities.map(([Icon, title, copy]) => (
          <article className="about-feature" key={title}>
            <span><Icon size={18} /></span>
            <div>
              <h4>{title}</h4>
              <p>{copy}</p>
            </div>
          </article>
        ))}
      </div>

      <section className="submit-site-card" id="submit-site">
        <div>
          <span className="eyebrow">Submit Site</span>
          <h3>提交你的中转站</h3>
          <p>填写公开可访问的站点首页或接口地址。提交后会进入待采集列表，后续刷新任务会自动探测状态、价格、公告和可用模型。</p>
        </div>
        <form className="submit-site-form" onSubmit={submitSite}>
          <label htmlFor="submit-site-url">站点地址</label>
          <div>
            <input
              id="submit-site-url"
              value={submitUrl}
              onChange={(event) => setSubmitUrl(event.target.value)}
              placeholder="https://api.example.com"
              autoComplete="url"
            />
            <button type="submit" disabled={submitState.status === "loading"}>
              {submitState.status === "loading" ? "获取中" : "提交"}
            </button>
          </div>
          {submitState.message && (
            <p className={`submit-site-message ${submitState.status}`}>{submitState.message}</p>
          )}
        </form>
      </section>

      <div className="about-process">
        {steps.map(([title, copy], index) => (
          <div className="about-step" key={title}>
            <strong>{String(index + 1).padStart(2, "0")}</strong>
            <span>{title}</span>
            <p>{copy}</p>
          </div>
        ))}
      </div>

      <div className="about-grid">
        <AboutCard id="about-data" icon={Database} title="数据来源">
          <p>候选站点来自互联网公开可见信息、手动补充和历史采集结果。采集器会读取站点公开接口，例如状态、价格、倍率、公告和性能摘要，再把能解析的部分归一化成站点、模型与公告三条主线。</p>
          <ul>
            <li>公开收集到的入口会先去重，再进入探测与解析流程。</li>
            <li>人工补充的站点会和自动收集结果合并处理。</li>
            <li>站点本身返回什么，页面就尽量保留什么；无响应或格式异常会标记为未知或部分可读。</li>
          </ul>
        </AboutCard>
        <AboutCard id="about-pricing" icon={Sparkles} title="比价口径">
          <p>模型比价以站点公开价格接口为准，尽量保留原始币种和计费方式。美元、人民币、按次调用会分开展示，不做强行汇率换算。</p>
          <ul>
            <li>按量价格展示输入、输出、缓存输入和缓存写入。</li>
            <li>按次模型展示每次请求价格。</li>
            <li>同一模型会按规范化名称聚合，站长自定义别名会作为别名保留。</li>
          </ul>
        </AboutCard>
        <AboutCard id="about-refresh" icon={RefreshCw} title="更新机制">
          <p>全量发现和短周期刷新分开运行。全量任务负责扩充候选站点范围，短周期任务负责刷新已有站点的接口数据。</p>
          <ul>
            <li>候选站点会定期重新发现、去重和探测。</li>
            <li>已收录站点会持续刷新状态、价格、公告和性能数据。</li>
            <li>数据更新会在校验完成后平滑上线，尽量减少页面访问中断。</li>
          </ul>
        </AboutCard>
        <AboutCard id="about-boundary" icon={ShieldCheck} title="使用边界">
          <p>中转站变化很快，价格、分组和可用性都可能在短时间内变化。本站提供的是采集时刻的公开信息索引，不代表实际购买建议。</p>
          <ul>
            <li>购买、充值和使用风险请以目标站点公告为准。</li>
            <li>成功率、延迟和 TPS 只在接口提供可解析数据时展示。</li>
            <li>本站不收集用户密钥，不代管账号，也不向第三方提交你的私有信息。</li>
          </ul>
        </AboutCard>
      </div>

      <section className="about-notice-grid">
        <article className="about-card about-disclaimer" id="about-disclaimer">
          <div className="about-card-title">
            <span><ShieldCheck size={18} /></span>
            <h4>免责声明</h4>
          </div>
          <p>本站所有内容均来自公开互联网、公开接口、公开 RSS 或用户主动提交的信息整理，仅用于信息索引与技术研究，不代表本站对任何第三方站点、服务、价格或可用性作出背书。</p>
          <p>如本站展示内容涉及错误、过期、冒犯、侵权或不希望被收录的情况，请通过下方问题反馈联系本站。收到有效反馈后，本站会尽快核对并处理，包括更正、隐藏或下架相关内容。</p>
        </article>

        <article className="about-card about-feedback" id="about-feedback">
          <div className="about-card-title">
            <span><MessageSquare size={18} /></span>
            <h4>问题反馈</h4>
          </div>
          <p>如果页面功能不好用、数据有误、希望下架内容，或者有新功能建议，可以在这里提交。反馈会记录提交时间，方便后续处理。</p>
          <form className="feedback-form" onSubmit={submitFeedback}>
            <label htmlFor="feedback-content">反馈内容</label>
            <textarea
              id="feedback-content"
              value={feedbackText}
              onChange={(event) => setFeedbackText(event.target.value)}
              placeholder="例如：某个站点信息不准确、页面筛选不好用、希望下架某条内容..."
              rows={5}
            />
            <label htmlFor="feedback-contact">联系方式，可选</label>
            <input
              id="feedback-contact"
              value={feedbackContact}
              onChange={(event) => setFeedbackContact(event.target.value)}
              placeholder="邮箱 / QQ / Telegram，方便需要时联系你"
            />
            <button type="submit" disabled={feedbackState.status === "loading"}>
              {feedbackState.status === "loading" ? "提交中" : "提交反馈"}
            </button>
            {feedbackState.message && (
              <p className={`submit-site-message ${feedbackState.status}`}>{feedbackState.message}</p>
            )}
          </form>
          <div className="feedback-list">
            <div className="feedback-list-head">
              <h5>最新反馈</h5>
              <input
                value={adminToken}
                onChange={(event) => setAdminToken(event.target.value)}
                placeholder="管理员口令，填写后可管理反馈"
                type="password"
              />
            </div>
            {feedbackItems.length ? feedbackItems.map((item) => (
              <div className="feedback-item" key={`${item.id || item.created_at}-${item.content}`}>
                <div>
                  <p>{item.content}</p>
                  <span>{formatStatusDateTime(item.created_at)} · {item.status === "new" ? "待处理" : item.status}</span>
                </div>
                {adminToken.trim() && item.id && (
                  <button type="button" onClick={() => deleteFeedback(item)}>处理</button>
                )}
              </div>
            )) : <p className="feedback-empty">暂无公开反馈</p>}
          </div>
        </article>
      </section>
    </section>
  );
}

function AboutCard({ id, icon: Icon, title, children }) {
  return (
    <article className="about-card" id={id}>
      <div className="about-card-title">
        <span><Icon size={18} /></span>
        <h4>{title}</h4>
      </div>
      {children}
    </article>
  );
}

function SiteCard({ site, layout, openSite }) {
  const cardRef = useRef(null);
  const [showAllProviders, setShowAllProviders] = useState(false);
  const [showAllGroups, setShowAllGroups] = useState(false);
  const [showAllModels, setShowAllModels] = useState(false);
  const providers = site.providers || [];
  const groups = site.groups || [];
  const models = site.models_preview || [];
  const baseLimits = useMemo(
    () => ({
      providers: layout === "list" ? 4 : 4,
      groups: layout === "list" ? 4 : 4,
      models: layout === "list" ? 5 : 8,
    }),
    [layout],
  );
  const [adaptiveLimits, setAdaptiveLimits] = useState(baseLimits);
  const providerLimit = showAllProviders ? providers.length : adaptiveLimits.providers;
  const groupLimit = showAllGroups ? groups.length : adaptiveLimits.groups;
  const listModelLimit = layout === "list" ? Math.min(5, adaptiveLimits.models) : adaptiveLimits.models;
  const modelLimit = showAllModels ? models.length : listModelLimit;
  const hasMoreProviders = providers.length > adaptiveLimits.providers;
  const hasMoreGroups = groups.length > adaptiveLimits.groups;
  const hasMoreModels = models.length > listModelLimit;
  const visibleProviders = showAllProviders ? providers : providers.slice(0, providerLimit);
  const visibleGroups = showAllGroups ? groups : groups.slice(0, groupLimit);
  const visibleModels = showAllModels ? models : models.slice(0, modelLimit);

  useEffect(() => {
    setAdaptiveLimits(baseLimits);
  }, [baseLimits, providers.length, groups.length, models.length]);

  useEffect(() => {
    const resetLimits = () => setAdaptiveLimits(baseLimits);
    window.addEventListener("resize", resetLimits);
    return () => window.removeEventListener("resize", resetLimits);
  }, [baseLimits]);

  useLayoutEffect(() => {
    if (layout !== "grid" || showAllProviders || showAllGroups || showAllModels) return undefined;
    const scheduled = [];
    const adapt = () => {
      const card = cardRef.current;
      const stats = card?.querySelector(".card-stats");
      const actions = card?.querySelector(".card-actions");
      if (!card || !stats || !actions) return;
      const gap = actions.getBoundingClientRect().top - stats.getBoundingClientRect().bottom;
      if (gap < 42) return;

      const next = { ...adaptiveLimits };
      const hidden = {
        providers: Math.max(0, providers.length - next.providers),
        groups: Math.max(0, groups.length - next.groups),
        models: Math.max(0, models.length - next.models),
      };
      if (!hidden.providers && !hidden.groups && !hidden.models) return;

      let slots = Math.min(4, Math.max(1, Math.ceil((gap - 30) / 20) * 2));
      const order = ["models", "providers", "groups"];
      while (slots > 0 && (hidden.models || hidden.providers || hidden.groups)) {
        let used = false;
        for (const key of order) {
          if (!hidden[key] || slots <= 0) continue;
          next[key] += 1;
          hidden[key] -= 1;
          slots -= 1;
          used = true;
        }
        if (!used) break;
      }

      if (
        next.providers !== adaptiveLimits.providers ||
        next.groups !== adaptiveLimits.groups ||
        next.models !== adaptiveLimits.models
      ) {
        setAdaptiveLimits(next);
      }
    };
    scheduled.push(window.requestAnimationFrame(adapt));
    scheduled.push(window.setTimeout(adapt, 80));
    scheduled.push(window.setTimeout(adapt, 180));
    scheduled.push(window.setTimeout(adapt, 500));
    scheduled.push(window.setTimeout(adapt, 1000));
    return () => {
      scheduled.forEach((id) => {
        window.cancelAnimationFrame(id);
        window.clearTimeout(id);
      });
    };
  }, [
    adaptiveLimits,
    groups.length,
    layout,
    models.length,
    providers.length,
    showAllGroups,
    showAllModels,
    showAllProviders,
  ]);

  return (
    <article ref={cardRef} className={`site-card ${layout}`}>
      <div className="site-main">
        <div className="site-title">
          <div className="favicon">{site.name?.slice(0, 1) || "R"}</div>
          <div>
            <h3>{site.name}</h3>
            <a href={site.origin} target="_blank" rel="noreferrer">{site.origin}</a>
          </div>
        </div>
        <span className={`status-badge ${site.status}`}><CheckCircle2 size={14} />{statusText(site.status)}</span>
      </div>
      {!!providers.length && (
        <div className={`chips provider-chips ${showAllProviders ? "expanded" : ""}`}>
          {visibleProviders.map((provider) => (
            <button className="provider-chip" key={provider} type="button" onClick={() => openSite(site.id, { type: "provider", value: provider })} title={`查看 ${displayProvider(provider)} 供应商下的模型价格`}>
              {displayProvider(provider)}
            </button>
          ))}
          {hasMoreProviders && (
            <button type="button" onClick={() => setShowAllProviders((value) => !value)} aria-expanded={showAllProviders} title={showAllProviders ? "收起供应商" : "展开全部供应商"}>
              {showAllProviders ? "收起" : `+${providers.length - providerLimit}`}
            </button>
          )}
        </div>
      )}
      {!!groups.length && (
        <div className={`chips group-chips ${showAllGroups ? "expanded" : ""}`}>
          {visibleGroups.map((group) => (
            <button key={group} type="button" onClick={() => openSite(site.id, { type: "group", value: group })} title={`查看 ${group} 分组下的模型价格`}>
              {group}
            </button>
          ))}
          {hasMoreGroups && (
            <button type="button" onClick={() => setShowAllGroups((value) => !value)} aria-expanded={showAllGroups} title={showAllGroups ? "收起分组" : "展开全部分组"}>
              {showAllGroups ? "收起" : `+${groups.length - groupLimit}`}
            </button>
          )}
        </div>
      )}
      <div className={`models ${showAllModels ? "expanded" : ""}`}>
        {visibleModels.map((model) => (
          <button key={model} type="button" onClick={() => openSite(site.id, { type: "model", value: model })} title={`查看 ${model} 的输入输出价格`}>
            {model}
          </button>
        ))}
        {hasMoreModels && (
          <button type="button" onClick={() => setShowAllModels((value) => !value)} aria-expanded={showAllModels} title={showAllModels ? "收起模型" : "展开预览模型"}>
            {showAllModels ? "收起" : `+${models.length - modelLimit}`}
          </button>
        )}
      </div>
      <div className="card-stats">
        <Stat label="模型" value={site.model_count || 0} />
        <Stat label="最低倍率" value={ratioUnitText(site.lowest_ratio)} />
        <Stat label="公告" value={site.notice || site.notifications ? "有" : "无"} />
      </div>
      <div className="card-actions">
        <button type="button" onClick={() => openSite(site.id)}>详情</button>
        <button type="button" onClick={() => navigator.clipboard.writeText(site.origin)}><Clipboard size={14} />复制</button>
        <a href={site.origin} target="_blank" rel="noreferrer"><ExternalLink size={14} />直达</a>
      </div>
    </article>
  );
}

function Stat({ label, value }) {
  return <div><strong>{value}</strong><span>{label}</span></div>;
}

function successText(value) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  return `${n.toFixed(Number.isInteger(n) ? 0 : 1)}%`;
}

function latencyText(value) {
  if (value === null || value === undefined) return "—";
  const s = Number(value) / 1000;
  return `${s.toFixed(s >= 10 ? 0 : 1)}s`;
}

function tpsText(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(1);
}

function siteHasPerf(site) {
  return site && (site.success_rate != null || site.avg_latency_ms != null || site.avg_tps != null);
}

function successClass(value) {
  if (value === null || value === undefined) return "perf-na";
  const n = Number(value);
  if (n >= 95) return "perf-good";
  if (n >= 80) return "perf-mid";
  return "perf-bad";
}

function latencyClass(value) {
  if (value === null || value === undefined) return "perf-na";
  return Number(value) <= 10000 ? "perf-good" : Number(value) <= 30000 ? "perf-mid" : "";
}

function prettyHost(origin) {
  return (origin || "").replace(/^https?:\/\//, "").replace(/\/$/, "");
}

function modelPerfForGroup(model, group) {
  const groupPerf = group ? model?.group_perf?.[group] : null;
  if (groupPerf) return groupPerf;
  if (group && model?.perf_group && group !== model.perf_group) {
    return {};
  }
  return {
    success_rate: model?.success_rate,
    avg_latency_ms: model?.avg_latency_ms,
    avg_tps: model?.avg_tps,
  };
}

function MetricValue({ compact, full, label }) {
  const [open, setOpen] = useState(false);
  const same = compact === full || full === "-" || !full;
  if (same) return <span>{compact}</span>;
  const slashIndex = compact.indexOf("/");
  const hasUnit = slashIndex > 0;
  const amount = hasUnit ? compact.slice(0, slashIndex) : compact;
  const unit = hasUnit ? compact.slice(slashIndex) : "";
  return (
    <span className="metric-pop">
      <button
        type="button"
        className={`metric-pop-trigger ${hasUnit ? "metric-split" : ""}`}
        title={`点击查看完整${label || "数值"}`}
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
      >
        {hasUnit ? (
          <>
            <span className="metric-main">{amount}</span>
            <span className="metric-unit">{unit}</span>
          </>
        ) : compact}
      </button>
      {open && (
        <span className="metric-popover" role="tooltip">
          <em>{label || "完整值"}</em>
          <strong>{full}</strong>
        </span>
      )}
    </span>
  );
}

function ModelRow({ model, modelSort, filters, openSite, openPriceDrawer }) {
  const [expanded, setExpanded] = useState(false);
  const [loadedSites, setLoadedSites] = useState(model.sites || []);
  const [loadingSites, setLoadingSites] = useState(false);
  const [siteError, setSiteError] = useState("");
  const previewLimit = 10;
  const allSites = loadedSites;
  const sites = expanded ? allSites : allSites.slice(0, previewLimit);
  const hasMoreRemoteSites = (model.site_count || 0) > allSites.length;

  useEffect(() => {
    setExpanded(false);
    setLoadedSites(model.sites || []);
    setLoadingSites(false);
    setSiteError("");
  }, [model.model, model.provider, modelSort, model.sites, filters?.minSuccess, filters?.maxLatency, filters?.minTps]);

  async function toggleExpanded() {
    if (expanded) {
      setExpanded(false);
      return;
    }
    if (hasMoreRemoteSites) {
      setLoadingSites(true);
      setSiteError("");
      try {
        const result = await api("/api/model-sites", {
          provider: model.provider,
          model: model.model,
          sort: modelSort,
          min_success: filters?.minSuccess,
          max_latency: filters?.maxLatency ? Number(filters.maxLatency) * 1000 : "",
          min_tps: filters?.minTps,
          page: 1,
          page_size: Math.min(Math.max(model.site_count || 10, 10), 2000),
        });
        setLoadedSites(result.items || []);
      } catch (error) {
        setSiteError(messageFromUnknown(error, "加载失败"));
        return;
      } finally {
        setLoadingSites(false);
      }
    }
    setExpanded(true);
  }

  async function openSiteModel(site) {
    if (!site?.site_id) {
      openPriceDrawer({ modelName: model.model, site });
      return;
    }
    try {
      await openSite(site.site_id, { type: "model", value: site.model || model.model });
    } catch (error) {
      openPriceDrawer({ modelName: model.model, site });
    }
  }

  return (
    <article className="model-row">
      <div className="model-head">
        <div>
          <h3>{model.model}</h3>
          <p>{displayProvider(model.provider)} · {model.site_count} 个站点</p>
        </div>
        <Sparkles size={18} />
      </div>
      <div className="model-table-wrap">
        <table className="model-table">
          <thead>
            <tr>
              <th className="t-site">站点</th>
              <th>输入</th>
              <th>输出</th>
              <th>缓存输入</th>
              <th>缓存写入</th>
              <th>倍率</th>
              <th>成功率</th>
              <th>延迟</th>
              <th>TPS</th>
              <th className="t-group">计价分组</th>
            </tr>
          </thead>
          <tbody>
            {sites.map((site) => {
              const groups = visibleGroupEntries(site);
              const primaryGroup = groups.visible.length ? groups.visible[0][0] : null;
              const inputText = isRequestBilled(site) ? modelPriceValueText(site, "request", null, true) : modelPriceValueText(site, "input", null, true);
              const outputText = modelPriceValueText(site, "output", null, true);
              const cacheInputText = modelPriceValueText(site, "cache_input", null, true);
              const cacheWriteText = modelPriceValueText(site, "cache_write", null, true);
              const multiplier = multiplierText(site, null, true);
              const fullInputText = isRequestBilled(site) ? modelPriceValueText(site, "request") : modelPriceValueText(site, "input");
              const fullOutputText = modelPriceValueText(site, "output");
              const fullCacheInputText = modelPriceValueText(site, "cache_input");
              const fullCacheWriteText = modelPriceValueText(site, "cache_write");
              const fullMultiplier = multiplierText(site);
              return (
                <tr key={`${site.origin}-${site.model}`}>
                  <td className="t-site">
                    <button
                      type="button"
                      className="t-site-link"
                      title={`查看 ${site.site_name} 的站点详情`}
                      onClick={() => openSiteModel(site)}
                    >
                      <span className="t-site-name">{site.site_name}</span>
                      <span className="t-site-host">{prettyHost(site.origin)}</span>
                    </button>
                  </td>
                  <td className="t-num"><MetricValue compact={inputText} full={fullInputText} label={isRequestBilled(site) ? "按次价格" : "输入价格"} /></td>
                  <td className="t-num"><MetricValue compact={outputText} full={fullOutputText} label="输出价格" /></td>
                  <td className="t-num t-dim"><MetricValue compact={cacheInputText} full={fullCacheInputText} label="缓存输入" /></td>
                  <td className="t-num t-dim"><MetricValue compact={cacheWriteText} full={fullCacheWriteText} label="缓存写入" /></td>
                  <td className="t-num"><MetricValue compact={multiplier} full={fullMultiplier} label="倍率" /></td>
                  <td className="t-num"><span className={`perf-pill ${successClass(site.success_rate)}`}>{successText(site.success_rate)}</span></td>
                  <td className={`t-num ${latencyClass(site.avg_latency_ms)}`}>{latencyText(site.avg_latency_ms)}</td>
                  <td className="t-num">{tpsText(site.avg_tps)}</td>
                  <td className="t-group">
                    <button
                      type="button"
                      className="t-group-btn"
                      title={`${visibleGroupText(site, 80)} · 查看站点公告、通知和该模型各分组价格`}
                      onClick={() => openSiteModel(site)}
                    >
                      <span>{primaryGroup || "-"}</span>
                      {groups.hidden > 0 && <em>+{groups.hidden}</em>}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {siteError && <div className="model-expand model-expand-note">{siteError}</div>}
      {(hasMoreRemoteSites || allSites.length > previewLimit) && (
        <button type="button" className="model-expand" onClick={toggleExpanded} disabled={loadingSites}>
          {loadingSites ? "加载中..." : expanded ? "收起" : `展开查看全部 ${model.site_count || allSites.length} 个站点`}
          <ChevronRight size={14} className={expanded ? "chev up" : "chev down"} />
        </button>
      )}
    </article>
  );
}

function MarkdownContent({ value, compact = false }) {
  return (
    <div className={`markdown-body ${compact ? "compact" : ""}`}>
      <ReactMarkdown
        components={{
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
          img: ({ node, ...props }) => <img {...props} loading="lazy" alt={props.alt || ""} />,
        }}
      >
        {value || ""}
      </ReactMarkdown>
    </div>
  );
}

function Announcement({ item }) {
  const [expanded, setExpanded] = useState(false);
  const content = item.content || "";
  const shouldCollapse = content.length > 260 || content.split("\n").length > 5;
  return (
    <article className="notice-row">
      <div className="notice-meta">
        <time>{timeText(item.first_seen_at)}</time>
        <span>{item.site_name}</span>
      </div>
      <div className="notice-body">
        <div className="notice-top">
          <a className="notice-site-link" href={item.origin} target="_blank" rel="noreferrer">{item.site_name}</a>
          <span>{item.origin}</span>
        </div>
        <div className="chips">{(item.tags || []).map((tag) => <span key={tag}>{tag}</span>)}</div>
        <MarkdownContent value={content} compact={shouldCollapse && !expanded} />
        {shouldCollapse && (
          <button className="notice-expand" type="button" onClick={() => setExpanded((value) => !value)}>
            {expanded ? "收起" : "展开全文"}
          </button>
        )}
      </div>
    </article>
  );
}

function defaultProtocolForText(model = "", provider = "") {
  const text = `${model || ""} ${provider || ""}`.toLowerCase();
  if (text.includes("gemini")) return "gemini";
  if (text.includes("claude") || text.includes("anthropic")) return "anthropic";
  return "openai";
}

function protocolForDetectionCategory(category) {
  if (category === "Claude") return "anthropic";
  if (category === "Gemini") return "gemini";
  return "openai";
}

function detectionCategoryForModelName(model = "") {
  const text = String(model || "").toLowerCase();
  if (text.includes("claude") || text.includes("anthropic")) return "Claude";
  if (text.includes("gemini")) return "Gemini";
  return "OpenAI";
}

function protocolForDetectionModel(model = "") {
  return protocolForDetectionCategory(detectionCategoryForModelName(model));
}

function suggestedBaseUrl(origin, protocol = "openai") {
  const value = (origin || "").trim().replace(/\/$/, "");
  if (!value) return "";
  const withProtocol = /^https?:\/\//i.test(value) ? value : `https://${value}`;
  return protocol === "anthropic" ? withProtocol : `${withProtocol}/v1`;
}

function originFromBaseUrl(baseUrl = "") {
  const value = String(baseUrl || "").trim();
  if (!value) return "";
  try {
    const parsed = new URL(/^https?:\/\//i.test(value) ? value : `https://${value}`);
    return `${parsed.protocol}//${parsed.host}`;
  } catch {
    return value.replace(/\/v1\/?$/i, "").replace(/\/$/, "");
  }
}

function detectionStatusMeta(status = "", name = "") {
  const value = String(status || "").toLowerCase();
  const itemName = String(name || "").toLowerCase();
  if (["pass", "passed", "ok", "success"].includes(value)) {
    return { label: "通过", tone: "pass", Icon: CheckCircle2 };
  }
  if (["warn", "warning", "marginal", "edge"].includes(value)) {
    return { label: "警告", tone: "warn", Icon: Gauge };
  }
  if (["fail", "failed", "error"].includes(value)) {
    return { label: "未通过", tone: "fail", Icon: X };
  }
  if (["skip", "skipped"].includes(value)) {
    if (itemName === "long_context") {
      return { label: "未启用", tone: "skip", Icon: Loader2 };
    }
    if (itemName.includes("token")) {
      return { label: "无法判断", tone: "unknown", Icon: Loader2 };
    }
    return { label: "跳过", tone: "skip", Icon: Loader2 };
  }
  if (["disabled", "not_enabled", "not-enabled"].includes(value)) {
    return { label: "未启用", tone: "skip", Icon: Loader2 };
  }
  if (["unknown", "inconclusive", "n/a", "na"].includes(value)) {
    return { label: "无法判断", tone: "unknown", Icon: Loader2 };
  }
  return { label: status || "未知", tone: "unknown", Icon: Loader2 };
}

const DETECTION_ITEM_LABELS = {
  identity: "身份一致性",
  behavioral_signature: "行为签名验证",
  thinking_signature: "思维签名验证",
  consistency: "模型一致性",
  knowledge: "知识准确度",
  pdf: "PDF 文档识别",
  basic_request: "基础请求",
  model_consistency: "模型一致性",
  model_info: "模型响应形状",
  protocol: "协议规范性",
  function_calling: "函数调用",
  structured_output: "结构化输出",
  integrity: "流式一致性",
  token_billing: "Token 计费",
  token_usage: "Token 用量",
  long_context: "长上下文真实性",
  message_id: "消息标识规范",
  streaming: "流式输出",
  stream_consistency: "流式一致性",
  usage: "Usage 字段",
  response_shape: "响应结构",
};

const DETECTION_DISPLAY_LABELS = {
  "basic request": "基础请求",
  "model consistency": "模型一致性",
  "function calling": "函数调用",
  "structured output": "结构化输出",
  protocol: "协议规范性",
  integrity: "流式一致性",
  "token billing": "Token 计费",
  "long context": "长上下文真实性",
};

const DETECTION_ITEM_ORDERS = {
  anthropic: [
    "identity",
    "behavioral_signature",
    "thinking_signature",
    "consistency",
    "knowledge",
    "pdf",
    "structured_output",
    "protocol",
    "integrity",
    "token_usage",
    "message_id",
    "long_context",
  ],
  openai: [
    "basic_request",
    "model_consistency",
    "function_calling",
    "structured_output",
    "protocol",
    "integrity",
    "token_billing",
    "long_context",
  ],
  gemini: [
    "basic_request",
    "model_info",
    "function_calling",
    "structured_output",
    "protocol",
    "integrity",
    "token_usage",
    "long_context",
  ],
};

function detectionItemName(itemOrName) {
  const displayName = typeof itemOrName === "object" && itemOrName
    ? itemOrName.display_name || itemOrName.displayName
    : "";
  const rawName = typeof itemOrName === "object" && itemOrName ? itemOrName.name : itemOrName;
  const displayKey = String(displayName || "").trim();
  if (displayKey) {
    const normalizedDisplay = displayKey.replace(/[_-]+/g, " ").trim().toLowerCase();
    if (DETECTION_DISPLAY_LABELS[normalizedDisplay]) return DETECTION_DISPLAY_LABELS[normalizedDisplay];
    if (/[\u4e00-\u9fa5]/.test(displayKey)) return displayKey;
  }
  const key = String(rawName || "").trim();
  if (!key) return "检测项";
  return DETECTION_ITEM_LABELS[key] || key.replace(/_/g, " ");
}

function detectionSummaryText(item) {
  const text = messageFromUnknown(item?.summary, "");
  if (!text || text === "null" || text === "undefined") return "";
  return text;
}

function orderedDetectionRows(rows, protocol) {
  const order = DETECTION_ITEM_ORDERS[String(protocol || "").toLowerCase()] || DETECTION_ITEM_ORDERS.openai;
  const rank = new Map(order.map((name, index) => [name, index]));
  return [...(rows || [])].sort((left, right) => {
    const leftRank = rank.has(left?.name) ? rank.get(left.name) : 999;
    const rightRank = rank.has(right?.name) ? rank.get(right.name) : 999;
    if (leftRank !== rightRank) return leftRank - rightRank;
    return String(left?.name || "").localeCompare(String(right?.name || ""));
  });
}

function detectionProtocolName(protocol) {
  const value = String(protocol || "").toLowerCase();
  if (value === "openai") return "OpenAI 协议";
  if (value === "anthropic" || value === "claude") return "Claude 协议";
  if (value === "gemini") return "Gemini 协议";
  return "协议";
}

function detectionScoreTone(score) {
  const value = Number(score);
  if (!Number.isFinite(value)) return { text: "—", label: "等待报告", tone: "unknown", color: "#94a3b8", percent: 0 };
  const normalized = value > 0 && value <= 1 ? value * 100 : value;
  const percent = Math.max(0, Math.min(100, normalized));
  if (percent >= 90) return { text: `${percent.toFixed(0)}%`, label: "协议表现良好", tone: "pass", color: "#0ea5e9", percent };
  if (percent >= 70) return { text: `${percent.toFixed(0)}%`, label: "存在可用风险", tone: "warn", color: "#f59e0b", percent };
  return { text: `${percent.toFixed(0)}%`, label: "需要谨慎使用", tone: "fail", color: "#ef4444", percent };
}

function qualityScoreTone(score) {
  if (score === null || score === undefined || score === "") {
    return { text: "—", label: "暂无质量评分", tone: "unknown", color: "#94a3b8", percent: 0 };
  }
  const meta = detectionScoreTone(score);
  if (meta.tone === "pass") return { ...meta, label: "质量表现较好" };
  if (meta.tone === "warn") return { ...meta, label: "质量存在风险" };
  if (meta.tone === "fail") return { ...meta, label: "质量风险较高" };
  return { ...meta, label: "暂无质量评分" };
}

function metricPathValue(source, path) {
  if (!source || typeof source !== "object") return undefined;
  return String(path).split(".").reduce((current, part) => {
    if (!current || typeof current !== "object") return undefined;
    return current[part];
  }, source);
}

function pickMetric(source, keys, { allowZero = false } = {}) {
  if (!source || typeof source !== "object") return null;
  for (const key of keys) {
    const value = metricPathValue(source, key);
    const num = Number(value);
    if (Number.isFinite(num) && (allowZero ? num >= 0 : num > 0)) return num;
  }
  return null;
}

function formatDetectionMetric(value, unit = "") {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return "—";
  if (unit === "ms") return `${Math.round(num).toLocaleString()}ms`;
  if (unit === "t/s") return `${num.toFixed(num >= 100 ? 0 : 1)} t/s`;
  return num.toLocaleString();
}

function detectionMetricCards(result) {
  const perf = result?.performance || {};
  const totalLatency = pickMetric(perf, ["total_latency_ms", "total_ms", "duration_ms", "latency_ms", "elapsed_ms"]);
  const outputTokens = pickMetric(perf, ["usage.output_tokens", "usage.completion_tokens", "output_tokens", "completion_tokens", "completionTokens"], { allowZero: true });
  const measuredTps = pickMetric(perf, ["tokens_per_second", "tps", "throughput", "throughput_tps"]);
  const computedTps = measuredTps || (outputTokens && totalLatency ? (outputTokens * 1000) / totalLatency : null);
  return [
    ["首 Token", formatDetectionMetric(pickMetric(perf, ["ttft_ms", "first_token_ms", "firstTokenMs", "time_to_first_token_ms"]), "ms")],
    ["总耗时", formatDetectionMetric(totalLatency, "ms")],
    ["吞吐", formatDetectionMetric(computedTps, "t/s")],
    ["输入 Token", formatDetectionMetric(pickMetric(perf, ["usage.input_tokens", "usage.prompt_tokens", "input_tokens", "prompt_tokens", "promptTokens"], { allowZero: true }))],
    ["输出 Token", formatDetectionMetric(outputTokens)],
  ];
}

function DetectionProgress({ status, message, result }) {
  const steps = [
    ["准备请求", "校验站点、Base URL 和模型参数"],
    ["协议探测", "向目标中转站发起实时检测请求"],
    ["能力检查", "验证响应结构、流式输出和工具调用能力"],
    ["报告生成", "汇总分数、风险项和性能指标"],
  ];
  const activeIndex = result ? steps.length : status === "running" ? 2 : status === "submitting" ? 1 : status === "error" ? 0 : 0;
  return (
    <aside className={`detect-live-card ${status}`}>
      <div className="detect-live-orbit" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div className="detect-live-copy">
        <span className="eyebrow">Live Check</span>
        <h3>{status === "running" ? "正在检测中" : result ? "报告已生成" : "等待开始检测"}</h3>
        <p>{message || "填写站点、模型和 API Key 后开始检测。"}</p>
      </div>
      <div className="detect-step-list">
        {steps.map(([title, desc], index) => {
          const done = result || index < activeIndex;
          const active = !result && index === activeIndex && (status === "running" || status === "submitting");
          return (
            <div className={`detect-step ${done ? "done" : ""} ${active ? "active" : ""}`} key={title}>
              <span>{done ? <CheckCircle2 size={16} /> : active ? <Loader2 className="spin" size={16} /> : index + 1}</span>
              <div>
                <strong>{title}</strong>
                <p>{desc}</p>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function DetectionPage() {
  const [origin, setOrigin] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [detectionCategory, setDetectionCategory] = useState("Claude");
  const [protocol, setProtocol] = useState("anthropic");
  const [mode, setMode] = useState("standard");
  const [apiKey, setApiKey] = useState("");
  const [detectionModels, setDetectionModels] = useState([]);
  const [loadingDetectionModels, setLoadingDetectionModels] = useState(false);
  const [modelFetchStatus, setModelFetchStatus] = useState("");
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");
  const activeDetectionRef = useRef(null);
  const modelAutoFetchRef = useRef("");
  const modelReady = detectionModels.length > 0 && detectionModels.includes(model);

  useEffect(() => {
    const nextProtocol = protocolForDetectionCategory(detectionCategory);
    if (protocol !== nextProtocol) setProtocol(nextProtocol);
  }, [detectionCategory]);

  useEffect(() => {
    const nextCategory = detectionCategoryForModelName(model);
    if (nextCategory !== detectionCategory) setDetectionCategory(nextCategory);
  }, [model]);

  async function loadDetectionModels({ silent = false } = {}) {
    const targetBaseUrl = (baseUrl || suggestedBaseUrl(origin, protocol)).trim();
    if (!targetBaseUrl || !apiKey.trim()) {
      setModelFetchStatus("请先填写 Base URL 和 API Key");
      return;
    }
    const fetchKey = `${targetBaseUrl}|${apiKey.trim().slice(0, 12)}`;
    modelAutoFetchRef.current = fetchKey;
    setLoadingDetectionModels(true);
    if (!silent) setModelFetchStatus("正在获取模型列表...");
    try {
      const payload = await postApi("/api/chat/models", { base_url: targetBaseUrl, api_key: apiKey });
      const nextModels = payload.items || [];
      setDetectionModels(nextModels);
      if (nextModels.length) {
        if (!model || !nextModels.includes(model)) {
          setModel(preferredModelFromList(nextModels, ""));
        }
        setModelFetchStatus(`已获取 ${nextModels.length} 个模型`);
      } else {
        setModel("");
        setModelFetchStatus("模型接口没有返回可选模型，不能开始检测");
      }
    } catch (error) {
      setDetectionModels([]);
      setModel("");
      setModelFetchStatus(messageFromUnknown(error, "模型获取失败，不能开始检测"));
    } finally {
      setLoadingDetectionModels(false);
    }
  }

  useEffect(() => {
    const targetBaseUrl = (baseUrl || "").trim();
    const key = apiKey.trim();
    if (!targetBaseUrl || key.length < 8) return undefined;
    const fetchKey = `${targetBaseUrl}|${key.slice(0, 12)}`;
    if (modelAutoFetchRef.current === fetchKey) return undefined;
    const timer = window.setTimeout(() => {
      loadDetectionModels({ silent: true });
    }, 900);
    return () => window.clearTimeout(timer);
  }, [baseUrl, apiKey]);

  useEffect(() => {
    if (!job?.job_id || status !== "running") return undefined;
    let cancelled = false;
    let inFlight = false;
    const timer = window.setInterval(async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const payload = await api(`/api/detections/${job.job_id}`);
        if (cancelled) return;
        setJob(payload.job || job);
        if (payload.result) {
          let nextResult = payload.result;
          const needsQuality = !nextResult.quality || ["pending", "skipped", "expired"].includes(nextResult.quality.status);
          const qualityRequest = activeDetectionRef.current;
          if (needsQuality && qualityRequest?.api_key) {
            setMessage("协议检测完成，正在生成质量实测...");
            try {
              const qualityPayload = await postApi(`/api/detections/${job.job_id}/quality`, qualityRequest, { timeoutMs: 180000 });
              if (!cancelled && qualityPayload.quality) {
                nextResult = {
                  ...nextResult,
                  quality: qualityPayload.quality,
                  ai_summary: qualityPayload.quality.ai_summary,
                };
              }
            } catch (qualityError) {
              if (!cancelled) {
                nextResult = {
                  ...nextResult,
                  quality: {
                    status: "done",
                    score: null,
                    level: "unknown",
                    risk_tags: ["质量实测未形成完整结论"],
                    ai_summary: messageFromUnknown(qualityError, "协议检测已完成，质量实测未形成完整结论"),
                    rows: [],
                  },
                };
              }
            }
          }
          setResult(nextResult);
          setStatus("done");
          setMessage("检测完成");
          window.clearInterval(timer);
        } else if (payload.job?.status === "error") {
          setStatus("error");
          setMessage(messageFromUnknown(payload.job?.error, "检测失败"));
          window.clearInterval(timer);
        }
      } catch (error) {
        if (!cancelled) {
          setStatus("error");
          setMessage(messageFromUnknown(error, "检测状态获取失败"));
          window.clearInterval(timer);
        }
      } finally {
        inFlight = false;
      }
    }, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [job?.job_id, status]);

  async function submitDetection(event) {
    event.preventDefault();
    const submitOrigin = (origin || originFromBaseUrl(baseUrl)).trim();
    if (!submitOrigin) {
      setStatus("error");
      setMessage("请填写 Base URL 或站点地址");
      return;
    }
    if (!modelReady) {
      setStatus("error");
      setMessage("请先成功获取模型列表，并从列表里选择目标模型");
      return;
    }
    if (!apiKey.trim()) {
      setStatus("error");
      setMessage("请填写 API Key");
      return;
    }
    setStatus("submitting");
    setMessage("正在提交检测任务...");
    setResult(null);
    try {
      const submitProtocol = protocol || protocolForDetectionModel(model);
      const requestPayload = {
        origin: submitOrigin,
        base_url: baseUrl || suggestedBaseUrl(submitOrigin, submitProtocol),
        model,
        provider: detectionCategory,
        protocol: submitProtocol,
        mode,
        api_key: apiKey,
      };
      activeDetectionRef.current = requestPayload;
      const payload = await postApi("/api/detections", requestPayload);
      setJob({ job_id: payload.job_id, status: "queued", ...payload });
      setStatus("running");
      setMessage("检测任务已创建，正在运行...");
    } catch (error) {
      setStatus("error");
      setMessage(messageFromUnknown(error, "检测任务提交失败"));
    }
  }

  return (
    <section className="detect-page">
      <div className="detect-workbench">
        <article className="detect-panel detect-form-panel" id="detect-form">
          <div className="detect-panel-head">
            <span><TestTubeDiagonal size={18} /></span>
            <div>
              <h3>检测参数</h3>
              <p>填写站点入口、目标模型和自己的 API Key。</p>
            </div>
          </div>
          <form className="detect-form" onSubmit={submitDetection}>
            <label className="detect-wide">
              <span>Base URL</span>
              <div className="detect-inline">
                <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1" />
                <button type="button" onClick={() => setBaseUrl(suggestedBaseUrl(origin || originFromBaseUrl(baseUrl), protocol))}>补齐</button>
              </div>
            </label>
            <label className="detect-wide">
              <span>API Key</span>
              <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="仅用于本次检测，不保存" />
            </label>
            <div className="detect-wide detect-model-card">
              <div className="detect-model-head">
                <div>
                  <span>目标模型</span>
                  <p>{modelFetchStatus || "填写 Base URL 和 API Key 后会自动尝试获取模型列表"}</p>
                </div>
                <button type="button" className="chat-model-button" onClick={() => loadDetectionModels()} disabled={loadingDetectionModels}>
                  {loadingDetectionModels ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
                  获取模型
                </button>
              </div>
              <div className="detect-model-grid">
                <label>
                  <span>模型选择</span>
                  <select value={model} onChange={(event) => setModel(event.target.value)} disabled={!detectionModels.length || loadingDetectionModels}>
                    {!detectionModels.length && <option value="">请先获取模型列表</option>}
                    {detectionModels.map((item) => <option key={item} value={item}>{item}</option>)}
                  </select>
                </label>
              </div>
            </div>
            <label>
              <span>检测协议</span>
              <select value={protocol} onChange={(event) => {
                const nextProtocol = event.target.value;
                setProtocol(nextProtocol);
                if (nextProtocol === "anthropic") setDetectionCategory("Claude");
                else if (nextProtocol === "gemini") setDetectionCategory("Gemini");
                else setDetectionCategory("OpenAI");
              }}>
                <option value="openai">OpenAI 兼容</option>
                <option value="anthropic">Anthropic 兼容</option>
                <option value="gemini">Gemini 兼容</option>
              </select>
            </label>
            <label>
              <span>检测强度</span>
              <select value={mode} onChange={(event) => setMode(event.target.value)}>
                <option value="quick">快速（省额度）</option>
                <option value="standard">标准（推荐）</option>
                <option value="full">完整（更全面）</option>
              </select>
              <small className="detect-help">
                {mode === "quick"
                  ? "只跑基础连通、模型回显和协议结构，消耗最少，适合先判断能不能用。"
                  : mode === "full"
                    ? "会尽量跑完整协议项；Claude 会多测行为签名/PDF 等能力，OpenAI/Gemini 目前主要接近标准模式。"
                    : "会跑基础连通、结构化输出、工具/函数调用、协议完整性、Token 统计和质量实测，日常推荐用这个。"}
              </small>
            </label>
            <label className="detect-wide">
              <span>站点地址（可选）</span>
              <input value={origin} onChange={(event) => setOrigin(event.target.value)} placeholder="不填会从 Base URL 自动识别，例如 https://api.example.com" />
            </label>
            <div className="detect-options">
              <div>
                <strong>Chat Completions</strong>
                <span>验证响应结构、usage 字段和流式兼容性</span>
              </div>
              <div>
                <strong>能力完整性</strong>
                <span>检查工具调用、JSON 输出和协议包装异常</span>
              </div>
              <div>
                <strong>性能采样</strong>
                <span>记录首 token、总耗时、吞吐和 token 统计</span>
              </div>
            </div>
            <div className="detect-actions">
              <button type="submit" disabled={status === "submitting" || status === "running" || !modelReady}>
                {status === "submitting" || status === "running" ? "检测中..." : modelReady ? "开始检测" : "请先获取模型"}
              </button>
              <button type="button" onClick={() => {
                setOrigin("");
                setBaseUrl("");
                setModel("");
                setDetectionCategory("Claude");
                setProtocol("anthropic");
                setMode("standard");
                setApiKey("");
                setDetectionModels([]);
                setModelFetchStatus("");
                setJob(null);
                setResult(null);
                activeDetectionRef.current = null;
                setStatus("idle");
                setMessage("");
              }}>
                清空
              </button>
            </div>
            {message && <p className={`detect-message ${status}`}>{message}</p>}
          </form>
        </article>

        <DetectionProgress status={status} message={message} result={result} />
      </div>

      <article className="detect-panel detect-report-panel" id="detect-result">
        <div className="detect-panel-head">
          <span><Sparkles size={18} /></span>
          <div>
            <h3>检测报告</h3>
            <p>结果来自目标站点的实时响应，不使用本站采集到的历史性能数据。</p>
          </div>
        </div>
        {result && <DetectionResult result={result} />}
        {!result && <div className="empty detect-empty">{status === "running" ? "检测运行中..." : "提交检测后会在这里显示报告"}</div>}
      </article>

      <article className="detect-panel detect-notes" id="detect-notes">
        <div className="detect-panel-head">
          <span><ShieldCheck size={18} /></span>
          <div>
            <h3>注意事项</h3>
            <p>检测只代表本次 API Key、当前模型和当前站点入口的实际表现。</p>
          </div>
        </div>
        <ul>
          <li>API Key 不会保存到 RelayWatch 数据库，检测任务结束后只保留脱敏报告。</li>
          <li>如果你的 Key 只开通了某个分组，检测结果也只代表该分组权限下的真实表现。</li>
          <li>快速模式成本最低；标准/完整模式会发起更多请求，速度更慢，也可能消耗更多额度。</li>
        </ul>
        <p className="detect-credit">
          本页面检测逻辑参考 <a href="https://github.com/canarybyte/veridrop" target="_blank" rel="noreferrer">canarybyte/veridrop</a>，感谢该项目提供的开源检测思路与支持。
        </p>
      </article>
    </section>
  );
}

function DetectionResult({ result }) {
  const scoreMeta = detectionScoreTone(result.total_score);
  const rows = orderedDetectionRows(result.results || [], result.protocol);
  const metricCards = detectionMetricCards(result);
  const quality = result.quality;
  const qualityReady = quality && !["pending", "skipped", "expired"].includes(quality.status);
  const qualityMeta = qualityScoreTone(qualityReady ? quality?.score : null);
  const qualityRows = (quality?.rows || []).filter((item) => item?.name !== "ai_summary");
  const flagged = rows.filter((item) => {
    const tone = detectionStatusMeta(item.status, item.name).tone;
    return tone === "warn" || tone === "fail";
  });
  const reportSummary = result.summary && result.summary !== quality?.ai_summary && !(flagged.length && /^[\s]*(优秀|良好|通过|合格|正常)[\s。.!！]*$/i.test(String(result.summary)))
    ? result.summary
    : "";
  return (
    <section className="detect-result">
      <article className="detect-report-box">
        <div className="detect-report-box-head">
          <div>
            <h4>协议检测报告</h4>
            <p>检查接口响应结构、流式一致性、工具/结构化输出、Token 统计和协议兼容性。</p>
          </div>
        </div>

        <div className="detect-report-grid">
          <div className={`detect-score-card ${scoreMeta.tone}`}>
            <div
              className="detect-score-ring"
              style={{ "--score": `${scoreMeta.percent}%`, "--score-color": scoreMeta.color }}
              aria-label={`协议检测评分 ${scoreMeta.text}`}
            >
              <strong>{scoreMeta.text}</strong>
              <span>{scoreMeta.label}</span>
            </div>
            <p>{result.tier_title || result.verdict || "协议检测"}</p>
            {result.base_url && <em>{result.base_url}</em>}
          </div>

          <div className="detect-checklist">
            {rows.length ? rows.map((item, index) => {
              const meta = detectionStatusMeta(item.status, item.name);
              const Icon = meta.Icon;
              const summary = detectionSummaryText(item);
              return (
                <div className={`detect-check-row ${meta.tone}`} key={`${item.name}-${index}`}>
                  <span><Icon size={17} className={meta.tone === "unknown" ? "spin-paused" : ""} /></span>
                  <strong>{detectionItemName(item)}</strong>
                  <em>{meta.label}</em>
                  {summary && meta.tone !== "pass" && <small>{summary}</small>}
                </div>
              );
            }) : (
              <div className="detect-check-row unknown">
                <span><Loader2 size={17} /></span>
                <strong>等待检测项</strong>
                <em>未开始</em>
              </div>
            )}
          </div>
        </div>

        {(reportSummary || result.run_error || flagged.length > 0) && (
          <div className={`detect-report-note ${flagged.some((item) => detectionStatusMeta(item.status, item.name).tone === "fail") ? "fail" : "warn"}`}>
            <strong>{flagged.length ? "协议检测摘要" : "协议检测说明"}</strong>
            {result.run_error && <p>{result.run_error}</p>}
            {reportSummary && <p>{reportSummary}</p>}
            {flagged.slice(0, 3).map((item, index) => (
              <p key={`${item.name}-${index}`}>
                <b>{detectionItemName(item)}：</b>{detectionSummaryText(item) || detectionStatusMeta(item.status, item.name).label}
              </p>
            ))}
          </div>
        )}

        <div className="detect-metrics">
          {metricCards.map(([label, value]) => (
            <div key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>

        <details className="detect-result-details">
          <summary>{detectionProtocolName(result.protocol)}检测项自检了什么？</summary>
          <div className="detect-result-list">
            {rows.map((item, index) => {
              const meta = detectionStatusMeta(item.status, item.name);
              return (
                <div className="detect-result-row" key={`${item.name}-${index}`}>
                  <strong>{detectionItemName(item)}</strong>
                  <span className={`detect-status ${meta.tone}`}>{meta.label}</span>
                  <em>{item.score === null || item.score === undefined ? "—" : `${Number(item.score).toFixed(1)}`}</em>
                  {detectionSummaryText(item) && <p>{detectionSummaryText(item)}</p>}
                </div>
              );
            })}
          </div>
        </details>
      </article>

      {qualityReady && (
        <article className="detect-report-box detect-quality-report">
          <div className="detect-report-box-head">
            <div>
              <h4>质量检测报告</h4>
              <p>用同一个 Key 真实调用模型，检查中文判断、基础推理、严格 JSON、代码输出和模型自述。</p>
            </div>
          </div>

          <div className="detect-report-grid">
            <div className={`detect-score-card ${qualityMeta.tone}`}>
              <div
                className="detect-score-ring"
                style={{ "--score": `${qualityMeta.percent}%`, "--score-color": qualityMeta.color }}
                aria-label={`质量检测评分 ${qualityMeta.text}`}
              >
                <strong>{qualityMeta.text}</strong>
                <span>{qualityMeta.label}</span>
              </div>
              <p>行为/质量验证</p>
              {(quality.response_model || quality.requested_model) && <em>{quality.response_model || quality.requested_model}</em>}
            </div>

            <div className="detect-checklist">
              {qualityRows.length ? qualityRows.map((item, index) => {
                const meta = detectionStatusMeta(item.status, item.name);
                const Icon = meta.Icon;
                return (
                  <div className={`detect-check-row ${meta.tone}`} key={`${item.name}-${index}`}>
                    <span><Icon size={17} /></span>
                    <strong>{item.display_name || detectionItemName(item)}</strong>
                    <em>{meta.label}</em>
                    {item.summary && <small>{item.summary}</small>}
                  </div>
                );
              }) : (
                <div className="detect-check-row unknown">
                  <span><Loader2 size={17} /></span>
                  <strong>等待质量检测</strong>
                  <em>未开始</em>
                </div>
              )}
            </div>
          </div>

          {quality.ai_summary && (
            <div className="detect-ai-summary">
              <span>AI 结论</span>
              <p>{quality.ai_summary}</p>
            </div>
          )}
          {!!quality.risk_tags?.length && (
            <div className="detect-quality-tags">
              {quality.risk_tags.map((tag) => <span key={tag}>{tag}</span>)}
            </div>
          )}
        </article>
      )}
    </section>
  );
}

function Pager({ page, pages, pageSize, total, setPage, setPageSize }) {
  const [targetPage, setTargetPage] = useState(String(page || 1));
  const safePages = Math.max(1, pages || 1);
  const safePage = Math.min(Math.max(1, page || 1), safePages);
  const pageSizeOptions = [12, 24, 30, 48, 60, 100];

  useEffect(() => {
    setTargetPage(String(safePage));
  }, [safePage]);

  function jumpToPage(event) {
    event?.preventDefault();
    const next = Math.min(Math.max(1, Number.parseInt(targetPage, 10) || 1), safePages);
    setTargetPage(String(next));
    setPage(next);
  }

  return (
    <div className="pager">
      <button type="button" disabled={safePage <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}><ChevronLeft size={16} />上一页</button>
      <span className="pager-page">{safePage} / {safePages}</span>
      <button type="button" disabled={safePage >= safePages} onClick={() => setPage((p) => Math.min(safePages, p + 1))}>下一页<ChevronRight size={16} /></button>
      <form className="pager-jump" onSubmit={jumpToPage}>
        <label htmlFor="pager-target">到</label>
        <input
          id="pager-target"
          type="number"
          min="1"
          max={safePages}
          value={targetPage}
          onChange={(event) => setTargetPage(event.target.value)}
        />
        <button type="submit">GO</button>
      </form>
      <label className="pager-size">
        <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}>
          {pageSizeOptions.map((size) => (
            <option key={size} value={size}>{size}/页</option>
          ))}
        </select>
      </label>
    </div>
  );
}

function PriceDrawer({ detail, onClose }) {
  if (!detail) return null;
  const { modelName, site } = detail;
  const groups = allGroupEntries(site);
  function renderGroupRow([group]) {
    const perf = modelPerfForGroup(site, group);
    return (
      <tr key={group}>
        <td>{group}</td>
        <td>{isRequestBilled(site) ? modelPriceValueText(site, "request", group) : modelPriceValueText(site, "input", group)}</td>
        <td>{modelPriceValueText(site, "output", group)}</td>
        <td>{modelPriceValueText(site, "cache_input", group)}</td>
        <td>{modelPriceValueText(site, "cache_write", group)}</td>
        <td>{multiplierText(site, group)}</td>
        <td><span className={`perf-pill ${successClass(perf.success_rate)}`}>{successText(perf.success_rate)}</span></td>
        <td className={latencyClass(perf.avg_latency_ms)}>{latencyText(perf.avg_latency_ms)}</td>
        <td>{tpsText(perf.avg_tps)}</td>
        <td>{billingText(site)}</td>
      </tr>
    );
  }
  return (
    <>
      <button className="scrim" type="button" onClick={onClose} aria-label="关闭价格详情" />
      <aside className="drawer price-drawer">
        <div className="drawer-head">
          <div>
            <h2>{site.site_name}</h2>
            <div className="drawer-origin-line">
              <span>{site.origin}</span>
              <a className="drawer-visit" href={site.origin} target="_blank" rel="noreferrer">
                <ExternalLink size={14} />访问站点
              </a>
            </div>
          </div>
          <button className="icon-button" type="button" onClick={onClose}><X size={17} /></button>
        </div>
        <div className="drawer-content">
          <section className="drawer-section">
            <div className="drawer-section-head">
              <h3><ArrowDownUp size={16} />模型：{modelName || site.model}</h3>
              <button type="button" onClick={onClose}>返回列表</button>
            </div>
            <div className="table-wrap price-detail-table-wrap">
              <table className="price-detail-table">
                <thead>
                  <tr>
                    <th>分组</th>
                    <th>输入价格</th>
                    <th>输出价格</th>
                    <th>缓存输入</th>
                    <th>缓存写入</th>
                    <th>倍率</th>
                    <th>成功率</th>
                    <th>延迟</th>
                    <th>TPS</th>
                    <th>计费</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map(renderGroupRow)}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </aside>
    </>
  );
}

function SiteDrawer({ site, focus = { type: "all" }, onClearFocus, onClose }) {
  if (!site) return null;
  const models = site.models || [];
  const activeGroup = focus.type === "group" ? focus.value : null;
  const filteredModels =
    focus.type === "group"
      ? models.filter((model) => (model.groups || []).includes(focus.value))
      : focus.type === "provider"
        ? models.filter((model) => model.provider === focus.value)
      : focus.type === "model"
        ? models.filter((model) => model.model === focus.value)
        : models;
  const sectionTitle =
    focus.type === "group"
      ? `分组：${focus.value}`
      : focus.type === "provider"
        ? `供应商：${displayProvider(focus.value)}`
      : focus.type === "model"
        ? `模型：${focus.value}`
        : "模型价格";
  const modelGroupTables =
    focus.type === "model"
      ? filteredModels.flatMap((model, modelIndex) =>
          allGroupEntries(model).map(([group], groupIndex) => ({
            group,
            rows: [{ model, group, key: `${model.model}-${model.provider}-${model.model_ratio}-${modelIndex}-${group}-${groupIndex}` }],
          })),
        )
      : [];
  const tableRows = filteredModels.map((model, index) => ({ model, group: activeGroup, key: `${model.model}-${model.provider}-${model.model_ratio}-${index}` }));
  return (
    <>
      <button className="scrim" type="button" onClick={onClose} aria-label="关闭详情" />
      <aside className="drawer">
        <div className="drawer-head">
          <div>
            <h2>{site.name}</h2>
            <div className="drawer-origin-line">
              <span>{site.origin}</span>
              <a className="drawer-visit" href={site.origin} target="_blank" rel="noreferrer">
                <ExternalLink size={14} />访问站点
              </a>
            </div>
          </div>
          <button className="icon-button" type="button" onClick={onClose}><X size={17} /></button>
        </div>
        <div className="drawer-content">
          <div className="drawer-summary">
            <Stat label="模型" value={site.model_count || 0} />
            <Stat label="最低倍率" value={ratioUnitText(site.lowest_ratio)} />
            <Stat label="状态" value={statusText(site.status)} />
          </div>
          {site.notice && (
            <section className="drawer-section">
              <h3><Bell size={16} />公告</h3>
              <MarkdownContent value={site.notice} />
            </section>
          )}
          {site.notifications && (
            <section className="drawer-section">
              <h3><Bell size={16} />通知</h3>
              <MarkdownContent value={site.notifications} />
            </section>
          )}
          <section className="drawer-section">
            <div className="drawer-section-head">
              <h3><ArrowDownUp size={16} />{sectionTitle}</h3>
              {focus.type !== "all" && (
                <button type="button" onClick={onClearFocus}>
                  全部模型
                </button>
              )}
            </div>
            {focus.type === "model" ? (
              modelGroupTables.map(({ group, rows }) => (
                <div className="model-group-table" key={group}>
                  <h4>分组：{group}</h4>
                  <div className="table-wrap">
                    <table>
                      <thead><tr><th>模型</th><th>供应商</th><th>输入价格</th><th>输出价格</th><th>缓存输入</th><th>缓存写入</th><th>倍率</th><th>成功率</th><th>延迟</th><th>TPS</th><th>计费</th></tr></thead>
                      <tbody>
                        {rows.map(({ model, group: rowGroup, key }) => {
                          const perf = modelPerfForGroup(model, rowGroup);
                          return (
                              <tr key={key}>
                                <td>{model.model}</td>
                                <td>{displayProvider(model.provider)}</td>
                                <td>{usagePriceText(model, inputPriceValue(model, rowGroup), rowGroup)}</td>
                                <td>{usagePriceText(model, outputPriceValue(model, rowGroup), rowGroup)}</td>
                                <td>{usagePriceText(model, cacheInputPriceValue(model, rowGroup), rowGroup)}</td>
                                <td>{usagePriceText(model, cacheWritePriceValue(model, rowGroup), rowGroup)}</td>
                                <td>{multiplierText(model, rowGroup)}</td>
                                <td><span className={`perf-pill ${successClass(perf.success_rate)}`}>{successText(perf.success_rate)}</span></td>
                                <td className={latencyClass(perf.avg_latency_ms)}>{latencyText(perf.avg_latency_ms)}</td>
                                <td>{tpsText(perf.avg_tps)}</td>
                                <td>{billingText(model)}</td>
                              </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))
            ) : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>模型</th><th>供应商</th><th>输入价格</th><th>输出价格</th><th>缓存输入</th><th>缓存写入</th><th>倍率</th><th>成功率</th><th>延迟</th><th>TPS</th><th>计费</th><th>分组</th></tr></thead>
                  <tbody>
                    {tableRows.map(({ model, group, key }) => {
                      const perf = modelPerfForGroup(model, group);
                      return (
                        <tr key={key}>
                          <td>{model.model}</td>
                          <td>{displayProvider(model.provider)}</td>
                          <td>{usagePriceText(model, inputPriceValue(model, group), group)}</td>
                          <td>{usagePriceText(model, outputPriceValue(model, group), group)}</td>
                          <td>{usagePriceText(model, cacheInputPriceValue(model, group), group)}</td>
                          <td>{usagePriceText(model, cacheWritePriceValue(model, group), group)}</td>
                          <td>{multiplierText(model, group)}</td>
                          <td><span className={`perf-pill ${successClass(perf.success_rate)}`}>{successText(perf.success_rate)}</span></td>
                          <td className={latencyClass(perf.avg_latency_ms)}>{latencyText(perf.avg_latency_ms)}</td>
                          <td>{tpsText(perf.avg_tps)}</td>
                          <td>{billingText(model)}</td>
                          <td>{group || (model.groups || []).join(", ")}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      </aside>
    </>
  );
}

createRoot(document.getElementById("root")).render(<App />);
