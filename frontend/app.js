const $ = (id) => document.getElementById(id);
let cases = [];
let activeResult = null;

const money = (value) => new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', maximumFractionDigits:0}).format(value || 0);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function request(url, options={}) {
  const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function selectedCase() { return cases.find(item => item.id === $('case-select').value); }
function renderCase() {
  const c = selectedCase(); if (!c) return;
  $('case-summary').innerHTML = `<div class="case-title">${escapeHtml(c.product)}</div><span class="case-tag">${escapeHtml(c.scenario)}</span>
    <div class="facts">
      <div class="fact"><small>FULFILLMENT NODE</small><b>${escapeHtml(c.node)}</b></div>
      <div class="fact"><small>OPEN ORDER</small><b>${escapeHtml(c.purchase_order.id)}</b></div>
      <div class="fact"><small>ORDER / CONFIRMED</small><b>${c.purchase_order.ordered_quantity} / ${c.purchase_order.confirmed_quantity}</b></div>
      <div class="fact"><small>ON HAND</small><b>${c.inventory.on_hand} units</b></div>
      <div class="fact"><small>DAILY DEMAND</small><b>${c.inventory.daily_demand} units</b></div>
      <div class="fact"><small>LEAD TIME</small><b>${c.supplier_lead_days} days</b></div>
      <div class="fact"><small>BUDGET REMAINING</small><b>${money(c.budget_remaining)}</b></div>
      <div class="fact"><small>STORAGE CAPACITY</small><b>${c.storage_capacity} units</b></div>
    </div>`;
  activeResult = null; $('result').classList.add('hidden'); $('empty-state').classList.remove('hidden');
  $('trace').className = 'trace-empty'; $('trace').textContent = 'Run an investigation to see which systems the agent consulted.';
  refreshEvents();
}

function renderTrace(trace) {
  $('trace-count').textContent = `${trace.length} calls`;
  $('trace').className = 'trace-list';
  $('trace').innerHTML = trace.map((step, i) => `<div class="trace-item"><span class="trace-number">${String(i+1).padStart(2,'0')}</span><div><b>${escapeHtml(step.tool)}</b><small>${escapeHtml(JSON.stringify(step.input))}</small></div></div>`).join('');
}

function renderDecision(output) {
  activeResult = output; const d = output.decision; const e = d.evidence;
  const decisionLabel = {supplemental_purchase:'Supplemental purchase suggested',no_action:'No additional purchase needed',escalate:'Buyer review needed'}[d.decision];
  const badge = d.decision === 'supplemental_purchase' ? 'green' : d.decision === 'escalate' ? 'amber' : 'gray';
  const metrics = [
    ['Demand + safety target', `${e.target_units_through_replenishment ?? 0} units`],
    ['Available supply', `${e.available_units_on_hand_plus_confirmed ?? 0} units`],
    ['Coverage gap', `${e.uncovered_units ?? 0} units`],
    ['Top-up estimate', e.estimated_cost ? money(e.estimated_cost) : '—']
  ];
  $('empty-state').classList.add('hidden'); $('result').classList.remove('hidden');
  $('result').innerHTML = `<div class="result-header"><div><div class="section-label">AGENT RECOMMENDATION</div><div class="result-title">${escapeHtml(decisionLabel)}</div><div class="result-copy">${escapeHtml(output.message)}</div></div><span class="badge ${badge}">${escapeHtml(d.decision.replaceAll('_',' '))}</span></div>
    <div class="evidence-grid">${metrics.map(([label,value])=>`<div class="metric"><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b></div>`).join('')}</div>
    <div class="reason-box">${escapeHtml(d.reason)}</div>
    ${d.constraints.length ? `<div class="constraint-list">${d.constraints.map(x=>`<span class="constraint-chip">Constraint: ${escapeHtml(x.replaceAll('_',' '))}</span>`).join('')}</div>` : ''}
    ${output.draft ? `<div class="action-row"><button class="button primary" id="approve-button">Approve &amp; place order <span>→</span></button><button class="button danger" id="fail-button">Simulate supplier rejection</button><span class="action-hint">Buyer approval required before action</span></div>` : ''}
    <div id="validation"></div>`;
  if (output.draft) {
    $('approve-button').onclick = () => approve(false);
    $('fail-button').onclick = () => approve(true);
  }
  renderTrace(output.trace || []);
}

async function investigate() {
  const button = $('analyze-button'); button.disabled = true; button.textContent = 'Investigating…';
  try { renderDecision(await request('/api/agent/run', {method:'POST', body:JSON.stringify({case_id:$('case-select').value})})); }
  catch (err) { $('empty-state').classList.add('hidden'); $('result').classList.remove('hidden'); $('result').innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`; }
  finally { button.disabled = false; button.innerHTML = 'Investigate situation <span>→</span>'; refreshEvents(); }
}

async function approve(simulateFailure) {
  const button = simulateFailure ? $('fail-button') : $('approve-button'); button.disabled = true; button.textContent = 'Processing…';
  try {
    const result = await request(`/api/drafts/${encodeURIComponent(activeResult.draft.id)}/approve`, {method:'POST', body:JSON.stringify({simulate_failure:simulateFailure})});
    const valid = result.validation.valid;
    $('validation').innerHTML = `<div class="validation ${valid?'ok':'fail'}"><b>${valid?'Validated':'Follow-up required'} · ${escapeHtml(result.validation.status)}</b><br>${escapeHtml(result.validation.message)}${result.action.purchase_order_id?` <span>Order ${escapeHtml(result.action.purchase_order_id)}</span>`:''}</div>`;
    renderTrace([...activeResult.trace, ...result.trace]);
  } catch(err) { $('validation').innerHTML = `<div class="validation fail">${escapeHtml(err.message)}</div>`; }
  finally { refreshEvents(); }
}

async function refreshEvents() {
  try {
    const events = await request(`/api/events?case_id=${encodeURIComponent($('case-select').value || '')}`);
    if (!events.length) { $('events').className='events-empty'; $('events').textContent='No actions recorded in this session.'; return; }
    $('events').className='events-list';
    $('events').innerHTML=events.slice(0,6).map(e=>`<div class="event"><b>${escapeHtml(e.type.replaceAll('_',' '))} · ${escapeHtml(e.payload.id || e.payload.draft_id || e.case_id)}</b><time>${new Date(e.created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time></div>`).join('');
  } catch (_) {}
}

async function runEvaluations() {
  const button=$('eval-button'); button.disabled=true; button.textContent='Evaluating…';
  try {
    const data=await request('/api/evals');
    $('eval-results').className='';
    $('eval-results').innerHTML=`<table class="eval-table"><thead><tr><th>SCENARIO</th><th>DECISION</th><th>CHECKS</th><th>RESULT</th></tr></thead><tbody>${data.results.map(r=>`<tr><td>${escapeHtml(r.id)}</td><td>${escapeHtml(r.decision)}</td><td>${Object.values(r.checks).filter(Boolean).length}/${Object.keys(r.checks).length}</td><td class="${r.passed?'pass':'fail'}">${r.passed?'PASS':'FAIL'}</td></tr>`).join('')}</tbody></table>`;
  } catch(err) { $('eval-results').textContent=err.message; }
  finally { button.disabled=false; button.textContent='Run evaluation ↗'; }
}

async function init() {
  cases = await request('/api/cases');
  $('case-select').innerHTML = cases.map(c=>`<option value="${escapeHtml(c.id)}">${escapeHtml(c.title)}</option>`).join('');
  $('case-select').onchange=renderCase; $('analyze-button').onclick=investigate; $('eval-button').onclick=runEvaluations;
  renderCase();
}
init().catch(err => { $('empty-state').innerHTML=`<div class="error">Could not load demo data: ${escapeHtml(err.message)}</div>`; });
