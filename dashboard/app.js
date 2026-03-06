/**
 * app.js — Dashboard Logic
 * Polls the /api/status endpoint every 3s and updates the UI in real-time.
 * Also renders a simulated demo flow when no backend is available.
 */

const API_BASE = window.location.hostname === 'localhost'
  ? 'http://localhost:8080'
  : '';

let totalScans = 0;
let deliveryCount = 0;
let alertCount = 0;
let events = [];
let demoMode = false;

// ── DOM refs ────────────────────────────────────────────────────────────────
const el = (id) => document.getElementById(id);

// ── Init ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  updateClock();
  setInterval(updateClock, 1000);
  pollStatus();
  setInterval(pollStatus, 3000);
});

function updateClock() {
  el('statusTime').textContent = new Date().toLocaleTimeString();
}

// ── API Polling ─────────────────────────────────────────────────────────────
async function pollStatus() {
  try {
    const resp = await fetch(`${API_BASE}/api/status`, { signal: AbortSignal.timeout(2500) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    demoMode = false;
    renderStatus(data);
  } catch {
    if (!demoMode) {
      demoMode = true;
      setStatus('demo', 'Demo Mode');
      runDemo();
    }
  }
}

function renderStatus(data) {
  // Stats
  totalScans = data.stats?.total_scans ?? totalScans + 1;
  deliveryCount = data.stats?.deliveries_detected ?? deliveryCount;
  alertCount = data.stats?.alerts_sent ?? alertCount;
  const avgMs = data.stats?.avg_processing_time_ms ?? 0;

  updateKPIs(totalScans, deliveryCount, alertCount, avgMs);
  setStatus('active', 'Monitoring Active');

  // State badge
  updateStateBadge(data.state ?? 'IDLE');

  // Detection bars
  updateBars(data.person_confidence ?? 0, data.package_confidence ?? 0);

  // Labels
  renderLabels(data.top_labels ?? []);

  // New event
  if (data.delivery_detected) {
    addEvent({
      type: 'delivery',
      title: '📦 Delivery Confirmed',
      time: new Date().toLocaleTimeString(),
      personConf: data.person_confidence,
      packageConf: data.package_confidence,
      labels: data.top_labels?.slice(0, 4) ?? [],
    });
    animatePipeline(true);
  } else {
    addEvent({
      type: 'scan',
      title: '🔍 Frame Scanned',
      time: new Date().toLocaleTimeString(),
      personConf: data.person_confidence,
      packageConf: data.package_confidence,
      labels: data.top_labels?.slice(0, 4) ?? [],
    });
    animatePipeline(false);
  }
}

// ── KPIs ────────────────────────────────────────────────────────────────────
function updateKPIs(scans, deliveries, alerts, avgMs) {
  animateNumber('kpiScansValue', scans);
  animateNumber('kpiDeliveriesValue', deliveries);
  animateNumber('kpiAlertsValue', alerts);
  el('kpiLatencyValue').textContent = avgMs ? `${Math.round(avgMs / 1000)}s` : '—';
  el('kpiScansSub').textContent = `Last scan: ${new Date().toLocaleTimeString()}`;
}

function animateNumber(id, target) {
  const elem = el(id);
  const current = parseInt(elem.textContent) || 0;
  if (current === target) return;
  const step = (target - current) / 10;
  let val = current;
  const interval = setInterval(() => {
    val += step;
    if ((step > 0 && val >= target) || (step < 0 && val <= target)) {
      clearInterval(interval);
      elem.textContent = target;
    } else {
      elem.textContent = Math.round(val);
    }
  }, 30);
}

// ── State Badge ─────────────────────────────────────────────────────────────
function updateStateBadge(state) {
  const badge = el('stateBadge');
  badge.textContent = state;
  badge.className = `state-badge ${state}`;
}

// ── Detection Bars ───────────────────────────────────────────────────────────
function updateBars(personConf, packageConf) {
  el('barPerson').style.width = `${Math.min(personConf, 100)}%`;
  el('barPersonVal').textContent = `${personConf.toFixed(0)}%`;
  el('barPackage').style.width = `${Math.min(packageConf, 100)}%`;
  el('barPackageVal').textContent = `${packageConf.toFixed(0)}%`;
}

// ── Labels ───────────────────────────────────────────────────────────────────
function renderLabels(labels) {
  const container = el('labelsContainer');
  container.innerHTML = '';
  if (!labels.length) {
    container.innerHTML = '<span class="label-placeholder">No labels detected</span>';
    return;
  }
  labels.slice(0, 8).forEach(l => {
    const name = l.name || l;
    const conf = l.confidence ?? l.conf ?? 0;
    const chip = document.createElement('span');
    chip.className = `label-chip ${conf >= 85 ? 'high' : ''}`;
    chip.textContent = `${name} ${conf ? conf.toFixed(0) + '%' : ''}`;
    container.appendChild(chip);
  });
}

// ── Event Feed ───────────────────────────────────────────────────────────────
function addEvent(ev) {
  events.unshift(ev);
  if (events.length > 50) events.pop();

  const feed = el('eventFeed');

  // Remove empty state
  const empty = feed.querySelector('.event-empty');
  if (empty) empty.remove();

  const card = document.createElement('div');
  card.className = `event-card ${ev.type === 'delivery' ? 'delivery' : ''}`;

  const tags = (ev.labels || []).slice(0, 3).map(l => {
    const name = l.name || l;
    const isP = name.toLowerCase() === 'person';
    const isPkg = ['package','box','cardboard'].includes(name.toLowerCase());
    return `<span class="event-tag ${isP ? 'person' : isPkg ? 'package' : ''}">${name}</span>`;
  }).join('');

  card.innerHTML = `
    <div class="event-icon">${ev.type === 'delivery' ? '🚚' : '👁'}</div>
    <div class="event-body">
      <div class="event-title">${ev.title}</div>
      <div class="event-time">${ev.time} · Person ${ev.personConf?.toFixed(0) ?? 0}% · Package ${ev.packageConf?.toFixed(0) ?? 0}%</div>
      <div class="event-tags">${tags}</div>
    </div>
  `;

  feed.insertBefore(card, feed.firstChild);
  el('eventsCount').textContent = `${events.length} event${events.length !== 1 ? 's' : ''}`;

  // Trim old cards
  while (feed.children.length > 20) feed.removeChild(feed.lastChild);
}

// ── Pipeline Animation ────────────────────────────────────────────────────────
function animatePipeline(isDelivery) {
  const steps = ['pipeCapture', 'pipeRek', 'pipeDetect', 'pipeTrigger', 'pipeNotify'];
  const allSteps = steps.map(id => el(id));

  allSteps.forEach(s => s.className = 'pipeline-step');

  steps.forEach((id, i) => {
    setTimeout(() => {
      allSteps.forEach(s => s.classList.remove('active'));
      const step = el(id);
      step.classList.add('active');
      if (i === steps.length - 1 && isDelivery) {
        step.classList.add('alert');
        setTimeout(() => {
          allSteps.forEach(s => { s.classList.remove('active', 'alert'); s.classList.add('done'); });
          setTimeout(() => allSteps.forEach(s => s.className = 'pipeline-step'), 2000);
        }, 800);
      }
    }, i * 400);
  });
}

// ── Status Indicator ─────────────────────────────────────────────────────────
function setStatus(state, label) {
  const dot = el('statusDot');
  dot.className = `status-dot ${state}`;
  el('statusLabel').textContent = label;
}

// ── Demo Mode ─────────────────────────────────────────────────────────────────
// Simulates a full delivery detection cycle for GitHub demo / no-backend mode
const DEMO_LABELS = [
  { name: 'Person', confidence: 92.4 },
  { name: 'Package', confidence: 87.1 },
  { name: 'Door', confidence: 78.3 },
  { name: 'House', confidence: 71.0 },
  { name: 'Outdoor', confidence: 68.5 },
  { name: 'Vegetation', confidence: 61.2 },
];

const DEMO_STATES = [
  { state: 'IDLE', person: 0, pkg: 0, delivery: false },
  { state: 'IDLE', person: 0, pkg: 0, delivery: false },
  { state: 'CANDIDATE', person: 82.1, pkg: 0, delivery: false },
  { state: 'CANDIDATE', person: 89.3, pkg: 72.4, delivery: false },
  { state: 'CONFIRMED', person: 92.4, pkg: 87.1, delivery: true },
  { state: 'ALERTED',   person: 92.4, pkg: 87.1, delivery: false },
  { state: 'COOLDOWN',  person: 0, pkg: 0, delivery: false },
  { state: 'COOLDOWN',  person: 0, pkg: 0, delivery: false },
];

let demoStep = 0;
let demoScanTotal = 0;
let demoDeliveries = 0;
let demoAlerts = 0;

function runDemo() {
  if (!demoMode) return;

  const step = DEMO_STATES[demoStep % DEMO_STATES.length];
  demoScanTotal++;

  if (step.delivery) { demoDeliveries++; demoAlerts++; }

  updateKPIs(demoScanTotal, demoDeliveries, demoAlerts, 2840);
  updateStateBadge(step.state);
  updateBars(step.person, step.pkg);
  renderLabels(step.person > 0 ? DEMO_LABELS : []);

  if (step.delivery) {
    addEvent({
      type: 'delivery',
      title: '📦 Delivery Confirmed (DEMO)',
      time: new Date().toLocaleTimeString(),
      personConf: step.person,
      packageConf: step.pkg,
      labels: DEMO_LABELS.slice(0, 4),
    });
    animatePipeline(true);
    setStatus('alert', 'Alert Sent');
  } else {
    addEvent({
      type: 'scan',
      title: `🔍 Frame Scanned — ${step.state} (DEMO)`,
      time: new Date().toLocaleTimeString(),
      personConf: step.person,
      packageConf: step.pkg,
      labels: step.person > 0 ? DEMO_LABELS.slice(0, 3) : [],
    });
    if (step.state !== 'IDLE') animatePipeline(false);
    setStatus('demo', 'Demo Mode — No Backend');
  }

  demoStep++;
  setTimeout(runDemo, 2500);
}
