const state = {
  data: null,
  selectedAsset: null,
  currentView: "chart",
  socket: null,
  audioContext: null,
  audioEnabled: false,
  chartSize: { width: 0, height: 0, dpr: 1 },
  _knownSignalIds: new Set(),
};

const els = {
  brokerStatus: document.getElementById("brokerStatus"),
  brokerError: document.getElementById("brokerError"),
  marketSearch: document.getElementById("marketSearch"),
  marketInput: document.getElementById("marketInput"),
  addMarketForm: document.getElementById("addMarketForm"),
  marketList: document.getElementById("marketList"),
  telegramTestButton: document.getElementById("telegramTestButton"),
  soundTestButton: document.getElementById("soundTestButton"),
  testFeedback: document.getElementById("testFeedback"),
  viewTabs: document.getElementById("viewTabs"),
  chartView: document.getElementById("chartView"),
  dashboardView: document.getElementById("dashboardView"),
  timeframeRow: document.getElementById("timeframeRow"),
  selectedAsset: document.getElementById("selectedAsset"),
  edgeStatus: document.getElementById("edgeStatus"),
  chart: document.getElementById("priceChart"),
  emptyChart: document.getElementById("emptyChart"),
  strengthValue: document.getElementById("strengthValue"),
  continuityValue: document.getElementById("continuityValue"),
  exhaustionValue: document.getElementById("exhaustionValue"),
  cciValue: document.getElementById("cciValue"),
  timeframeValue: document.getElementById("timeframeValue"),
  signalsList: document.getElementById("signalsList"),
  signalCount: document.getElementById("signalCount"),
  perfTotal: document.getElementById("perfTotal"),
  perfWinRate: document.getElementById("perfWinRate"),
  perfWins: document.getElementById("perfWins"),
  perfLosses: document.getElementById("perfLosses"),
  perfPending: document.getElementById("perfPending"),
  perfAvgScore: document.getElementById("perfAvgScore"),
  shadowTotal: document.getElementById("shadowTotal"),
  shadowWinRate: document.getElementById("shadowWinRate"),
  shadowPending: document.getElementById("shadowPending"),
  dashboardBalance: document.getElementById("dashboardBalance"),
  dashboardBankruptcies: document.getElementById("dashboardBankruptcies"),
  dashboardTargets: document.getElementById("dashboardTargets"),
  learningExamples: document.getElementById("learningExamples"),
  learningShadowExamples: document.getElementById("learningShadowExamples"),
  learningMinRate: document.getElementById("learningMinRate"),
  learningBlocked: document.getElementById("learningBlocked"),
  learningBlockRecommendations: document.getElementById("learningBlockRecommendations"),
  learningExploration: document.getElementById("learningExploration"),
  learningAllowed: document.getElementById("learningAllowed"),
  brokerTradingEnabled: document.getElementById("brokerTradingEnabled"),
  brokerTradesPlaced: document.getElementById("brokerTradesPlaced"),
  brokerTradesFailed: document.getElementById("brokerTradesFailed"),
  brokerTradingMode: document.getElementById("brokerTradingMode"),
  brokerConnectButton: document.getElementById("brokerConnectButton"),
  brokerTradeList: document.getElementById("brokerTradeList"),
  supabaseStatus: document.getElementById("supabaseStatus"),
  learningStatus: document.getElementById("learningStatus"),
  learningPatterns: document.getElementById("learningPatterns"),
  marketStats: document.getElementById("marketStats"),
  directionStats: document.getElementById("directionStats"),
  resultList: document.getElementById("resultList"),
  liveConfidence: document.getElementById("liveConfidence"),
  liveAnalysis: document.getElementById("liveAnalysis"),
  virtualBalance: document.getElementById("virtualBalance"),
  balanceMode: document.getElementById("balanceMode"),
  balanceHistory: document.getElementById("balanceHistory"),
  liveLearning: document.querySelector(".live-learning"),
  metricsRow: document.querySelector(".metrics-row"),
};

const timeframeLabels = new Map([
  [30, "30s"],
  [45, "45s"],
  [60, "1m"],
  [120, "2m"],
  [180, "3m"],
  [300, "5m"],
]);

window.appState = state;

function connectSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  state.socket = new WebSocket(`${protocol}://${window.location.host}/ws`);

  state.socket.addEventListener("message", (event) => {
    state.data = JSON.parse(event.data);
    detectNewSignalsAndNotify(state.data.signals || []).catch(() => {});
    ensureSelectedAsset();
    render();
  });

  state.socket.addEventListener("close", () => {
    setTimeout(connectSocket, 1500);
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await responseErrorText(response));
  }
  state.data = await response.json();
  ensureSelectedAsset();
  render();
}

async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await responseErrorText(response));
  }
  return await response.json();
}

async function refreshState() {
  try {
    const data = await apiRequest("/api/state");
    state.data = data;
    ensureSelectedAsset();
    render();
  } catch (error) {
    // The websocket reconnect path will keep trying too.
  }
}

async function responseErrorText(response) {
  const text = await response.text();
  try {
    const payload = JSON.parse(text);
    return payload.detail || payload.message || text;
  } catch (error) {
    return text || `HTTP ${response.status}`;
  }
}

function ensureSelectedAsset() {
  const markets = state.data?.markets || [];
  if (!markets.length) {
    state.selectedAsset = null;
    return;
  }
  if (!state.selectedAsset || !markets.includes(state.selectedAsset)) {
    state.selectedAsset = markets[0];
  }
}

function render() {
  if (!state.data) return;
  safeRender(renderStatus);
  safeRender(renderTimeframes);
  safeRender(renderMarkets);
  safeRender(renderSnapshot);
  safeRender(renderSignals);
  safeRender(renderDashboard);
  safeRender(renderVirtualBalance);
}

function safeRender(fn) {
  try {
    fn();
  } catch (error) {
    console.error(`Render failed in ${fn.name}`, error);
  }
}

function renderStatus() {
  els.brokerStatus.textContent = state.data.broker_status || "Sin estado";
  if (state.data.last_error) {
    els.brokerStatus.title = state.data.last_error;
    els.brokerError.textContent = state.data.last_error;
    els.brokerError.title = state.data.last_error;
  } else {
    els.brokerError.textContent = "";
    els.brokerError.title = "";
  }
}

function renderTimeframes() {
  const timeframe = Number(state.data.timeframe || 60);
  els.timeframeValue.textContent = timeframeLabels.get(timeframe) || `${timeframe}s`;
  els.timeframeRow.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.timeframe) === timeframe);
  });
}

function renderMarkets() {
  const query = els.marketSearch.value.trim().toLowerCase();
  const active = new Set(state.data.active_markets || []);
  const markets = (state.data.markets || []).filter((asset) => asset.toLowerCase().includes(query));

  els.marketList.innerHTML = "";
  markets.forEach((asset) => {
    const item = document.createElement("article");
    item.className = `market-item${asset === state.selectedAsset ? " selected" : ""}`;

    const nameWrap = document.createElement("div");
    const name = document.createElement("button");
    name.className = "market-name";
    name.type = "button";
    name.textContent = asset;
    name.addEventListener("click", () => {
      state.selectedAsset = asset;
      state.currentView = "chart";
      renderView();
      render();
    });
    const meta = document.createElement("div");
    meta.className = "market-meta";
    meta.textContent = active.has(asset) ? "activo" : "pausado";
    nameWrap.append(name, meta);

    const switchLabel = document.createElement("label");
    switchLabel.className = "switch";
    switchLabel.title = active.has(asset) ? "Pausar mercado" : "Activar mercado";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = active.has(asset);
    checkbox.addEventListener("change", () => {
      api(`/api/markets/${encodeURIComponent(asset)}/enabled`, {
        method: "POST",
        body: JSON.stringify({ enabled: checkbox.checked }),
      });
    });
    const slider = document.createElement("span");
    switchLabel.append(checkbox, slider);

    const remove = document.createElement("button");
    remove.className = "market-remove";
    remove.type = "button";
    remove.title = "Eliminar mercado";
    remove.textContent = "x";
    remove.addEventListener("click", () => {
      api(`/api/markets/${encodeURIComponent(asset)}`, { method: "DELETE" });
    });

    item.append(nameWrap, switchLabel, remove);
    els.marketList.appendChild(item);
  });
}

function renderSnapshot() {
  const snapshot = state.data.snapshots?.[state.selectedAsset];
  els.selectedAsset.textContent = state.selectedAsset || "-";

  if (!snapshot) {
    els.edgeStatus.textContent = "MERCADO SIN VENTAJA ESTADISTICA";
    els.edgeStatus.classList.remove("edge");
    els.strengthValue.textContent = "0.0";
    els.continuityValue.textContent = "0.0";
    els.exhaustionValue.textContent = "0.0";
    els.cciValue.textContent = "0.0";
    els.liveConfidence.textContent = "Descartada";
    els.liveAnalysis.textContent = "Esperando vela cerrada para analizar.";
    if (state.currentView === "chart") {
      drawChart([], [], null);
    }
    return;
  }

  const hasSignal = Boolean(snapshot.signal);
  els.edgeStatus.textContent = hasSignal
    ? `${snapshot.signal.direction} · ${snapshot.signal.score}/10`
    : snapshot.market_message;
  els.edgeStatus.classList.toggle("edge", hasSignal);
  els.strengthValue.textContent = Number(snapshot.strength || 0).toFixed(1);
  els.continuityValue.textContent = Number(snapshot.continuity || 0).toFixed(1);
  els.exhaustionValue.textContent = Number(snapshot.exhaustion || 0).toFixed(1);
  els.cciValue.textContent = Number(snapshot.cci || 0).toFixed(1);
  els.liveConfidence.textContent = confidenceLabel(snapshot.confidence, snapshot.stake_amount);
  els.liveAnalysis.textContent = snapshot.analysis_text || snapshot.market_message || "Esperando vela cerrada para analizar.";
  if (state.currentView === "chart") {
    drawChart(snapshot.candles || [], snapshot.zones || [], snapshot.signal || null);
  }
}

function renderSignals() {
  const signals = state.data.signals || [];
  const total = Number(state.data.signal_history_total || signals.length);
  els.signalCount.textContent = total > signals.length ? `${signals.length}/${total}` : String(total);
  els.signalCount.title =
    total > signals.length ? `Mostrando ${signals.length} de ${total} senales guardadas` : `${total} senales guardadas`;
  els.signalsList.innerHTML = "";

  if (!signals.length) {
    const empty = document.createElement("p");
    empty.className = "market-meta";
    empty.textContent = "Sin señales válidas todavía";
    els.signalsList.appendChild(empty);
    return;
  }

  signals.forEach((signal) => {
    const item = document.createElement("article");
    item.className = `signal-item ${signal.direction.toLowerCase()}`;
    const time = new Date(signal.created_at).toLocaleTimeString();
    item.innerHTML = `
      <div class="signal-head">
        <span class="signal-direction">${signal.direction} · ${signal.asset}</span>
        <span class="signal-score">${signal.score}/10</span>
      </div>
      <div class="signal-body">
        <span><strong>Expiración:</strong> ${signal.suggested_expiration}s</span>
        <span><strong>Apuesta:</strong> ${formatMoney(signal.stake_amount || 0)} · <strong>Entrada:</strong> ${signal.pending_execution_at ? new Date(signal.pending_execution_at).toLocaleTimeString() : "siguiente vela"}</span>
        <span><strong>Confianza:</strong> ${confidenceLabel(signal.confidence, signal.stake_amount)} · <strong>Puntuación:</strong> ${signal.factor_score || 0}/6</span>
        <span><strong>Fuerza:</strong> ${Number(signal.strength).toFixed(1)} · <strong>Continuidad:</strong> ${Number(signal.continuity).toFixed(1)}</span>
        <span><strong>Cansancio:</strong> ${Number(signal.exhaustion).toFixed(1)}</span>
        <span><strong>CCI(20):</strong> ${Number(signal.cci || 0).toFixed(1)}</span>
        <span>${signal.main_reason}</span>
        <span class="signal-time">${time}</span>
      </div>
    `;
    item.addEventListener("click", () => {
      state.selectedAsset = signal.asset;
      state.currentView = "chart";
      renderView();
      render();
    });
    els.signalsList.appendChild(item);
  });
}

function renderDashboard() {
  if (!els.perfTotal) return;
  const perf = state.data.performance || {};
  els.perfTotal.textContent = String(perf.total || 0);
  els.perfWinRate.textContent = `${Number(perf.win_rate || 0).toFixed(1)}%`;
  els.perfWins.textContent = String(perf.wins || 0);
  els.perfLosses.textContent = String(perf.losses || 0);
  els.perfPending.textContent = String(perf.pending || 0);
  els.perfAvgScore.textContent = Number(perf.avg_score || 0).toFixed(1);
  if (els.shadowTotal) els.shadowTotal.textContent = String(perf.shadow_total || 0);
  if (els.shadowWinRate) els.shadowWinRate.textContent = `${Number(perf.shadow_win_rate || 0).toFixed(1)}%`;
  if (els.shadowPending) els.shadowPending.textContent = String(perf.shadow_pending || 0);
  const balance = state.data.virtual_balance || {};
  if (els.dashboardBalance) els.dashboardBalance.textContent = formatMoney(balance.balance || 0);
  if (els.dashboardBankruptcies) els.dashboardBankruptcies.textContent = String(balance.bankruptcies || 0);
  if (els.dashboardTargets) els.dashboardTargets.textContent = String(balance.targets_hit || 0);
  renderLearning(state.data.learning || {});
  renderBuckets(els.marketStats, perf.by_market || [], "Sin operaciones evaluadas por mercado");
  renderBuckets(els.directionStats, perf.by_direction || [], "Sin operaciones evaluadas por dirección");
  renderResults(perf.recent_results || []);
  renderBrokerTrades(state.data.broker_trading || {});
  renderSupabase(state.data.supabase || {});
}

function renderLearning(learning) {
  if (!els.learningExamples || !els.learningPatterns) return;
  els.learningExamples.textContent = String(learning.resolved_examples || 0);
  if (els.learningShadowExamples) els.learningShadowExamples.textContent = String(learning.shadow_examples || 0);
  els.learningMinRate.textContent = `${Number(learning.min_win_rate || 0).toFixed(1)}%`;
  els.learningBlocked.textContent = String(learning.blocked_signals || 0);
  els.learningBlockRecommendations.textContent = String(learning.block_recommendations || learning.blocked_signals || 0);
  els.learningExploration.textContent = String(learning.exploration_signals || 0);
  els.learningAllowed.textContent = String(learning.allowed_signals || 0);
  els.learningStatus.textContent = learning.enabled ? "Activo" : "Pausado";
  renderLearningPatterns(learning);
}

function renderLearningPatterns(learning) {
  els.learningPatterns.innerHTML = "";
  const patterns = learning.risky_patterns || [];
  if (learning.last_decision) {
    const row = document.createElement("article");
    row.className = "stats-row";
    row.innerHTML = `
      <div class="stats-head">
        <span>Ultima decision</span>
      </div>
      <div class="stats-meta">${learning.last_decision}</div>
    `;
    els.learningPatterns.appendChild(row);
  }
  if (!patterns.length) {
    const empty = document.createElement("p");
    empty.className = "market-meta";
    empty.textContent = "Aun no hay patrones riesgosos con suficientes muestras.";
    els.learningPatterns.appendChild(empty);
    return;
  }
  patterns.forEach((pattern) => {
    const row = document.createElement("article");
    row.className = "stats-row";
    row.innerHTML = `
      <div class="stats-head">
        <span>${pattern}</span>
      </div>
    `;
    els.learningPatterns.appendChild(row);
  });
}

function renderVirtualBalance() {
  const balance = state.data?.virtual_balance || {};
  els.virtualBalance.textContent = formatMoney(balance.balance || 0);
  els.balanceMode.textContent = balance.mode || "Proteccion normal";
  els.balanceHistory.innerHTML = "";
  const history = balance.history || [];
  if (!history.length) {
    const empty = document.createElement("p");
    empty.className = "market-meta";
    empty.textContent = "Sin eventos de saldo todavia.";
    els.balanceHistory.appendChild(empty);
    return;
  }

  history.slice(-12).reverse().forEach((event) => {
    const row = document.createElement("article");
    row.className = `balance-event ${balanceEventClass(event.mark)}`;
    const time = new Date(event.timestamp).toLocaleTimeString();
    row.innerHTML = `
      <strong><span>${event.mark}</span><span>${formatMoney(event.balance || 0)}</span></strong>
      <span>${time} · ${event.asset || "-"} ${event.direction && event.direction !== "NONE" ? event.direction : ""}</span>
      <span>Apuesta ${formatMoney(event.stake_amount || 0)} · Resultado ${event.result || "-"} · P/L ${formatSignedMoney(event.profit || 0)}</span>
    `;
    els.balanceHistory.appendChild(row);
  });
}

function confidenceLabel(confidence, stake) {
  if (confidence === "high") return `Alta ${formatMoney(stake || 20000)}`;
  if (confidence === "low") return `Baja ${formatMoney(stake || 10000)}`;
  return "Descartada";
}

function balanceEventClass(mark = "") {
  const value = String(mark).toLowerCase();
  if (value.includes("ganancia")) return "ganancia";
  if (value.includes("perdida")) return "perdida";
  if (value.includes("quiebra")) return "quiebra";
  if (value.includes("meta")) return "meta";
  if (value.includes("pausa")) return "pausa";
  return "";
}

function renderBuckets(container, buckets, emptyText) {
  container.innerHTML = "";
  if (!buckets.length) {
    const empty = document.createElement("p");
    empty.className = "market-meta";
    empty.textContent = emptyText;
    container.appendChild(empty);
    return;
  }

  buckets.forEach((bucket) => {
    const row = document.createElement("article");
    row.className = "stats-row";
    const winRate = Number(bucket.win_rate || 0);
    row.innerHTML = `
      <div class="stats-head">
        <span>${bucket.name}</span>
        <strong>${winRate.toFixed(1)}%</strong>
      </div>
      <div class="stats-bar"><span style="width: ${Math.max(0, Math.min(100, winRate))}%"></span></div>
      <div class="stats-meta">
        ${bucket.wins || 0}G · ${bucket.losses || 0}P · ${bucket.pushes || 0}E · ${bucket.pending || 0} pendientes
      </div>
    `;
    container.appendChild(row);
  });
}

function renderResults(results) {
  els.resultList.innerHTML = "";
  if (!results.length) {
    const empty = document.createElement("p");
    empty.className = "market-meta";
    empty.textContent = "Aun no hay señales registradas para evaluar.";
    els.resultList.appendChild(empty);
    return;
  }

  results.forEach((result) => {
    const row = document.createElement("article");
    row.className = `result-row ${result.status}${result.is_shadow ? " shadow" : ""}`;
    const created = new Date(result.created_at).toLocaleTimeString();
    const entry = Number(result.entry_price || 0);
    const exit = result.result_price == null ? null : Number(result.result_price);
    const blockedReason = result.is_shadow && result.blocked_reason ? result.blocked_reason : "";
    row.innerHTML = `
      <div class="result-head">
        <span>${result.direction} · ${result.asset}</span>
        <span class="result-status">${statusLabel(result.status)}</span>
      </div>
      <div class="result-meta">
        ${created} · score ${result.score}/10 · CCI ${Number(result.cci || 0).toFixed(1)}
      </div>
      <div class="result-meta">
        Entrada ${formatPrice(entry)} · Salida ${exit == null ? "-" : formatPrice(exit)}
      </div>
      <div class="result-meta">
        Apuesta ${formatMoney(result.stake_amount || 0)} · Saldo ${result.balance_after == null ? "-" : formatMoney(result.balance_after)}
      </div>
      ${blockedReason ? `<div class="result-meta shadow-reason">Bloqueada: ${blockedReason}</div>` : ""}
      ${result.abort_reason ? `<div class="result-meta">Abortada: ${result.abort_reason}</div>` : ""}
    `;
    els.resultList.appendChild(row);
  });
}

function renderBrokerTrades(brokerTrading) {
  if (!els.brokerTradeList) return;
  const enabled = Boolean(brokerTrading.enabled);
  els.brokerTradingEnabled.textContent = enabled ? "ON" : "OFF";
  els.brokerTradingEnabled.style.color = enabled ? "var(--green)" : "var(--muted)";
  els.brokerTradesPlaced.textContent = String(brokerTrading.placed || 0);
  els.brokerTradesFailed.textContent = String(brokerTrading.failed || 0);
  if (els.brokerConnectButton) {
    els.brokerConnectButton.textContent = enabled ? "Desconectar broker" : "Conectar broker";
    els.brokerConnectButton.classList.toggle("active", enabled);
    els.brokerConnectButton.title = enabled
      ? "Apagar envio de nuevas operaciones al broker"
      : "Duplicar en el broker las operaciones aprobadas por el saldo virtual";
  }
  els.brokerTradingMode.textContent = `${brokerTrading.balance_mode || "PRACTICE"} Â· ventana ${Number(brokerTrading.entry_window_seconds || 0).toFixed(1)}s`;
  els.brokerTradeList.innerHTML = "";
  const trades = brokerTrading.recent_trades || [];
  if (!trades.length) {
    const empty = document.createElement("p");
    empty.className = "market-meta";
    empty.textContent = enabled ? "Esperando entradas validadas por el saldo virtual." : "Broker real desconectado; sigue operando solo el saldo virtual.";
    els.brokerTradeList.appendChild(empty);
    return;
  }

  trades.slice(-20).reverse().forEach((trade) => {
    const row = document.createElement("article");
    row.className = `result-row ${trade.status === "placed" ? "win" : "loss"}`;
    const time = new Date(trade.placed_at || trade.requested_at).toLocaleTimeString();
    row.innerHTML = `
      <div class="result-head">
        <span>${trade.asset} ${trade.direction} Â· ${formatMoney(trade.stake_amount || 0)}</span>
        <span class="result-status">${trade.status === "placed" ? "ENVIADA" : "FALLO"}</span>
      </div>
      <div class="result-meta">
        ${time} Â· ${trade.balance_mode || "-"} Â· Orden ${trade.broker_order_id || "-"} Â· Exp ${trade.expiration_seconds || 0}s
      </div>
      ${trade.error ? `<div class="result-meta">Error: ${trade.error}</div>` : ""}
    `;
    els.brokerTradeList.appendChild(row);
  });
}

function renderSupabase(supabase) {
  if (!els.supabaseStatus) return;
  const active = Boolean(supabase.enabled) && Boolean(supabase.connected);
  els.supabaseStatus.textContent = active ? "ON" : "OFF";
  els.supabaseStatus.style.color = active ? "var(--green)" : "var(--muted)";
  els.supabaseStatus.title = supabase.last_error || (supabase.enabled ? "Supabase configurado" : "Supabase no configurado");
}

function statusLabel(status) {
  if (status === "win") return "GANADA";
  if (status === "loss") return "PERDIDA";
  if (status === "push") return "EMPATE";
  if (status === "aborted") return "ABORTADA";
  if (status === "waiting_entry") return "ESPERANDO APERTURA";
  return "PENDIENTE";
}

function formatMoney(value) {
  const amount = Number(value || 0);
  return `$${Math.round(amount).toLocaleString("es-CO")}`;
}

function formatSignedMoney(value) {
  const amount = Number(value || 0);
  const prefix = amount > 0 ? "+" : amount < 0 ? "-" : "";
  return `${prefix}${formatMoney(Math.abs(amount))}`;
}

function formatPrice(value) {
  if (!Number.isFinite(value)) return "-";
  return value > 10 ? value.toFixed(4) : value.toFixed(6);
}

function renderView() {
  const chartActive = state.currentView === "chart";
  els.chartView.classList.toggle("hidden", !chartActive);
  els.dashboardView.classList.toggle("hidden", chartActive);
  if (els.liveLearning) els.liveLearning.classList.toggle("hidden", !chartActive);
  if (els.metricsRow) els.metricsRow.classList.toggle("hidden", !chartActive);
  els.viewTabs.querySelectorAll("button[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.currentView);
  });
  if (chartActive) {
    state.chartSize = { width: 0, height: 0, dpr: 1 };
  }
}

function drawChart(candles, zones, signal) {
  const canvas = els.chart;
  const parent = canvas.parentElement;
  const rect = parent.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = Math.max(1, Math.floor(rect.width));
  const cssHeight = Math.max(1, Math.floor(rect.height));
  if (
    state.chartSize.width !== cssWidth ||
    state.chartSize.height !== cssHeight ||
    state.chartSize.dpr !== dpr
  ) {
    canvas.width = Math.max(1, Math.floor(cssWidth * dpr));
    canvas.height = Math.max(1, Math.floor(cssHeight * dpr));
    canvas.style.width = `${cssWidth}px`;
    canvas.style.height = `${cssHeight}px`;
    state.chartSize = { width: cssWidth, height: cssHeight, dpr };
  }

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  const data = candles.slice(-70);
  els.emptyChart.classList.toggle("visible", data.length < 4);
  if (data.length < 4) return;

  const pad = { top: 24, right: 58, bottom: 28, left: 18 };
  const width = cssWidth - pad.left - pad.right;
  const height = cssHeight - pad.top - pad.bottom;
  const highs = data.map((candle) => Number(candle.high));
  const lows = data.map((candle) => Number(candle.low));
  let max = Math.max(...highs);
  let min = Math.min(...lows);
  const rawSpan = max - min;
  const absMax = Math.abs(max) || 1;
  const absMin = Math.abs(min) || 1;
  const minSpan = Math.max(absMax * 0.002, absMin * 0.002, 0.0005);
  const span = Math.max(rawSpan, minSpan);
  max += span * 0.08;
  min -= span * 0.08;

  const y = (price) => pad.top + ((max - price) / (max - min)) * height;
  const x = (index) => pad.left + (index / Math.max(data.length - 1, 1)) * width;
  const candleWidth = Math.max(4, Math.min(12, (width / data.length) * 0.58));

  drawGrid(ctx, { width: cssWidth, height: cssHeight }, pad, min, max, y);
  drawZones(ctx, zones, y, pad.left, width);

  data.forEach((candle, index) => {
    const open = Number(candle.open);
    const close = Number(candle.close);
    const high = Number(candle.high);
    const low = Number(candle.low);
    const cx = x(index);
    const up = close >= open;
    const color = up ? "#16835a" : "#c2413a";
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(cx, y(high));
    ctx.lineTo(cx, y(low));
    ctx.stroke();
    const bodyTop = y(Math.max(open, close));
    const bodyHeight = Math.max(2, Math.abs(y(open) - y(close)));
    ctx.fillRect(cx - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
  });

  if (signal) {
    drawSignal(ctx, signal, data, x, y);
  }
}

function drawGrid(ctx, rect, pad, min, max, y) {
  ctx.strokeStyle = "#e5e9ef";
  ctx.fillStyle = "#667085";
  ctx.lineWidth = 1;
  ctx.font = "12px system-ui";
  for (let i = 0; i <= 4; i += 1) {
    const price = min + ((max - min) * i) / 4;
    const py = y(price);
    ctx.beginPath();
    ctx.moveTo(pad.left, py);
    ctx.lineTo(rect.width - pad.right, py);
    ctx.stroke();
    ctx.fillText(price.toFixed(price > 10 ? 3 : 5), rect.width - pad.right + 8, py + 4);
  }
}

function drawZones(ctx, zones, y, left, width) {
  zones.forEach((zone) => {
    const py = y(Number(zone.price));
    const isSupport = zone.kind === "support";
    ctx.fillStyle = isSupport ? "rgba(22, 131, 90, 0.10)" : "rgba(194, 65, 58, 0.10)";
    ctx.strokeStyle = isSupport ? "rgba(22, 131, 90, 0.45)" : "rgba(194, 65, 58, 0.45)";
    ctx.fillRect(left, py - 7, width, 14);
    ctx.beginPath();
    ctx.moveTo(left, py);
    ctx.lineTo(left + width, py);
    ctx.stroke();
  });
}

function drawSignal(ctx, signal, data, x, y) {
  const index = data.findIndex(
    (candle) => Math.floor(candle.timestamp) === Math.floor(new Date(signal.created_at).getTime() / 1000),
  );
  const lastIndex = index >= 0 ? index : data.length - 1;
  const px = x(lastIndex);
  const py = y(Number(signal.price));
  const isCall = signal.direction === "CALL";
  ctx.fillStyle = isCall ? "#16835a" : "#c2413a";
  ctx.beginPath();
  if (isCall) {
    ctx.moveTo(px, py - 22);
    ctx.lineTo(px - 9, py - 5);
    ctx.lineTo(px + 9, py - 5);
  } else {
    ctx.moveTo(px, py + 22);
    ctx.lineTo(px - 9, py + 5);
    ctx.lineTo(px + 9, py + 5);
  }
  ctx.closePath();
  ctx.fill();
}

els.addMarketForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const asset = els.marketInput.value.trim();
  if (!asset) return;
  els.marketInput.value = "";
  api("/api/markets", { method: "POST", body: JSON.stringify({ asset }) });
});

els.marketSearch.addEventListener("input", renderMarkets);
els.telegramTestButton.addEventListener("click", testTelegram);
els.soundTestButton.addEventListener("click", testSound);
if (els.brokerConnectButton) {
  els.brokerConnectButton.addEventListener("click", toggleBrokerTrading);
}

els.viewTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (!button) return;
  state.currentView = button.dataset.view;
  renderView();
  render();
});

els.timeframeRow.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-timeframe]");
  if (!button) return;
  api("/api/timeframe", {
    method: "POST",
    body: JSON.stringify({ timeframe: Number(button.dataset.timeframe) }),
  });
});

document.addEventListener(
  "pointerdown",
  () => {
    state.audioEnabled = true;
    ensureAudioContext().catch(() => {});
  },
  { once: true },
);

window.addEventListener("resize", () => renderSnapshot());
renderView();
connectSocket();
setInterval(refreshState, 3000);

async function detectNewSignalsAndNotify(signals) {
  try {
    const ids = new Set(signals.map((s) => s.id));
    const hasNewSignal = signals.some((s) => !state._knownSignalIds.has(s.id));
    state._knownSignalIds = ids;
    if (hasNewSignal) {
      await playNotification();
    }
  } catch (e) {
    // ignore notification failures
  }
}

async function playNotification(force = false) {
  if (!force && !state.audioEnabled) {
    return;
  }
  try {
    const ctx = await ensureAudioContext();
    if (!ctx || ctx.state !== "running") {
      throw new Error("Audio no disponible");
    }
    await playTone(ctx, 880, 0.16, 0.08);
    await playTone(ctx, 1180, 0.18, 0.09);
  } catch (e) {
    try {
      const audio = new Audio();
      audio.src = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAIlYAAESsAAACABAAZGF0YQAAAAA=";
      audio.volume = 0.35;
      await Promise.race([audio.play(), new Promise((resolve) => setTimeout(resolve, 300))]);
    } catch (error) {
      // browser blocked fallback audio
    }
  }
}

function playTone(ctx, frequency, duration, volume) {
  return new Promise((resolve) => {
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    const now = ctx.currentTime;
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(frequency, now);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(volume, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.addEventListener("ended", resolve, { once: true });
    oscillator.start(now);
    oscillator.stop(now + duration + 0.02);
    setTimeout(resolve, (duration + 0.08) * 1000);
  });
}

async function ensureAudioContext() {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return null;
  if (!state.audioContext) {
    state.audioContext = new AudioCtx();
  }
  if (state.audioContext.state === "suspended") {
    try {
      await Promise.race([
        state.audioContext.resume(),
        new Promise((resolve) => setTimeout(resolve, 300)),
      ]);
    } catch (e) {
      // user gesture required
    }
  }
  return state.audioContext.state === "running" ? state.audioContext : null;
}

async function testSound() {
  els.soundTestButton.disabled = true;
  setTestFeedback("Probando sonido...", false);
  try {
    state.audioEnabled = true;
    await playNotification(true);
    setTestFeedback("Sonido de prueba enviado.", false);
  } catch (error) {
    setTestFeedback("No se pudo reproducir sonido. Usa el boton otra vez.", true);
  } finally {
    els.soundTestButton.disabled = false;
  }
}

async function testTelegram() {
  els.telegramTestButton.disabled = true;
  setTestFeedback("Enviando prueba de Telegram...", false);
  try {
    const response = await apiRequest("/api/telegram/test", { method: "POST" });
    if (response?.sent) {
      setTestFeedback("Prueba de Telegram enviada.", false);
    } else {
      setTestFeedback("No se recibio respuesta de Telegram.", true);
    }
  } catch (error) {
    setTestFeedback(`Error Telegram: ${error.message || String(error)}`, true);
  } finally {
    els.telegramTestButton.disabled = false;
  }
}

async function toggleBrokerTrading() {
  if (!state.data || !els.brokerConnectButton) return;
  const current = Boolean(state.data.broker_trading?.enabled);
  els.brokerConnectButton.disabled = true;
  setTestFeedback(current ? "Desconectando broker..." : "Conectando broker...", false);
  try {
    const data = await apiRequest("/api/broker/trading", {
      method: "POST",
      body: JSON.stringify({ enabled: !current }),
    });
    state.data = data;
    ensureSelectedAsset();
    render();
    setTestFeedback(!current ? "Broker real conectado." : "Broker real desconectado.", false);
  } catch (error) {
    setTestFeedback(`Error broker: ${error.message || String(error)}`, true);
  } finally {
    els.brokerConnectButton.disabled = false;
  }
}

function setTestFeedback(message, isError = false) {
  if (!els.testFeedback) return;
  els.testFeedback.textContent = message;
  els.testFeedback.classList.toggle("error", isError);
}
