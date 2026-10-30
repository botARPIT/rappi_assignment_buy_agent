const $ = id => document.getElementById(id);
let cases = [];
let activeResult = null;
let busy = false;
const money = value => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(value || 0);
const moneyExact = value => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(value);
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const constraintLabel = value => ({
  storage_capacity:'Would exceed the available storage space.',
  budget:'Would exceed the remaining budget.',
  supplier_capacity:'Supplier cannot provide the requested quantity.',
  arrival_too_late:'Would arrive too late to prevent a stock shortage.',
  insufficient_coverage:'Would still leave demand uncovered.',
  supplier_feedback_requires_refresh:'Supplier terms need to be refreshed first.',
  supplier_unavailable:'Supplier is currently unavailable.',
  higher_cost:'Costs more than the selected supplier.',
  longer_lead_time:'Arrives later than the selected supplier.',
  supplier_id_tiebreak:'Another supplier was selected as the tie-breaker.'
}[value] || `${String(value ?? '').replaceAll('_',' ').replace(/^./,c=>c.toUpperCase())}.`);

async function request(url, options={}) {
  const response=await fetch(url,{headers:{'Content-Type':'application/json'},...options});
  const data=await response.json();
  if(!response.ok) throw new Error(typeof data.error==='string'?data.error:JSON.stringify(data.detail || data));
  return data;
}
const selectedCase=()=>cases.find(c=>c.id===$('case-select').value);
function renderCaseSummary() {
  const c=selectedCase();if(!c)return;
  const feature={recommendation_review:'Review an existing order plan',supplier_shortfall:'Supplier may send less than ordered',demand_change:'Demand has changed'}[c.feature];
  $('case-summary').innerHTML=`<div class="case-title">${esc(c.product)}</div><span class="case-tag">${esc(feature)}</span><div class="facts">
    <div class="fact"><small>LOCATION</small><b>${esc(c.node)}</b></div><div class="fact"><small>IN STOCK NOW</small><b>${c.inventory.on_hand} units</b></div>
    <div class="fact"><small>ORDERED / SUPPLIER CONFIRMED</small><b>${c.purchase_order.ordered_quantity} / ${c.purchase_order.confirmed_quantity} units</b></div><div class="fact"><small>EXPECTED ARRIVAL</small><b>In ${c.purchase_order.eta_days} days</b></div>
    <div class="fact"><small>DAILY DEMAND · FORECAST / RECENT</small><b>${c.demand.forecast_daily} / ${c.demand.recent_daily} units</b></div><div class="fact"><small>RECENT SALES DATA</small><b>${c.demand.observed_days} days · ${c.demand.age_hours} hours old</b></div>
    <div class="fact"><small>BUDGET LEFT</small><b>${money(c.budget_cents/100)}</b></div><div class="fact"><small>STORAGE ROOM</small><b>${c.storage_capacity} units</b></div>
    ${c.recommended_quantity!==null?`<div class="fact"><small>CURRENT ORDER PLAN</small><b>${c.recommended_quantity} units</b></div>`:''}
    ${c.additional_incoming.length?`<div class="fact"><small>OTHER STOCK ON THE WAY</small><b>${c.additional_incoming.reduce((sum,r)=>sum+r.quantity,0)} units</b></div>`:''}</div>`;
}
function renderCase() {
  renderCaseSummary();
  activeResult=null;$('result').classList.add('hidden');$('empty-state').classList.remove('hidden');
  $('trace').className='trace-empty';$('trace').textContent='Review a situation to see which records and systems were checked.';
  $('trace-count').textContent='0 calls';refreshEvents();
}
function renderTrace(trace) {
  const checkNames={get_purchasing_situation:'Read situation details',get_inventory_and_open_orders:'Check stock and incoming orders',get_demand_evidence:'Check demand and recent sales',get_supplier_terms:'Check supplier options and budget',evaluate_purchase_policy:'Apply purchasing rules',prepare_purchase_draft:'Prepare an order for your review'};
  $('trace-count').textContent=`${trace.length} calls`;$('trace').className='trace-list';
  $('trace').innerHTML=trace.map((step,i)=>`<div class="trace-item"><span class="trace-number">${String(i+1).padStart(2,'0')}</span><details><summary>${esc(checkNames[step.tool]||step.tool)}</summary><pre>${esc(JSON.stringify({input:step.input,result:step.result},null,2))}</pre></details></div>`).join('');
}
function renderDecision(output) {
  activeResult=output;const d=output.decision,e=d.evidence;
  const labels={accept:'Accept the purchase recommendation',modify:'Modify the purchase recommendation',reject:'Reject unnecessary buying',investigate:'More evidence is needed',supplemental_purchase:'Prepare a supplemental purchase',adjust_plan:'Adjust the purchasing plan',no_action:'No additional purchase needed',escalate:'Buyer intervention is needed'};
  const review=output.agent_recommendation, comparison=output.recommendation_comparison;
  const reviewLabels={buy:'Buy',no_buy:'No purchase',needs_review:'Needs review'};
  const comparisonLabels={agree:'Recommendations agree · both reviews reach the same decision',disagree:'Recommendations differ · please check the details',unavailable:'Independent review unavailable · please check the details'};
  const metrics=[['Stock needed through arrival',`${e.target_units_through_replenishment ?? '—'} units`],['Stock available (including confirmed orders)',`${e.available_units_on_hand_plus_confirmed ?? '—'} units`],['Potential shortfall',`${e.uncovered_units ?? '—'} units`],['Suggested purchase',d.quantity?`${d.quantity} units · ${money(d.estimated_cost)}`:'No purchase']];
  $('empty-state').classList.add('hidden');$('result').classList.remove('hidden');
  $('result').innerHTML=`<div class="result-header"><div><div class="section-label">STEP 2 · RECOMMENDATION AND REASONS</div><div class="result-title">${esc(labels[d.decision]||d.decision)}</div><div class="result-copy">${esc(output.summary||d.reason)}</div></div><span class="badge ${d.requires_human_approval?'green':d.decision==='escalate'?'amber':'gray'}">${d.requires_human_approval?'Needs your approval':d.decision==='escalate'?'Needs review':'No order proposed'}</span></div>
    <div class="evidence-grid">${metrics.map(([label,value])=>`<div class="metric"><small>${esc(label)}</small><b>${esc(value)}</b></div>`).join('')}</div>
    ${comparison?`<section class="recommendation-comparison"><div class="section-label">SECOND REVIEW</div><div class="comparison-status ${comparison.status==='agree'?'agree':'review'}">${esc(comparisonLabels[comparison.status]||'Please review the details')}</div><div class="comparison-grid"><div class="comparison-card"><strong>Recommended plan</strong><p>${esc(labels[d.decision]||d.decision)}</p><small>${d.quantity?`${d.quantity} units · ${esc(d.supplier)} · ${moneyExact(d.estimated_cost)}`:'No purchase proposed'}${e.budget_before_order!==undefined?`<br>Budget before purchase: ${moneyExact(e.budget_before_order)}<br>Estimated budget after full delivery: ${moneyExact(e.projected_budget_after_full_confirmation)}`:e.budget_remaining!==undefined?`<br>Budget available: ${moneyExact(e.budget_remaining)}`:''}</small></div><div class="comparison-card"><strong>Independent agent recommendation · cross-check</strong>${review?`<p>${esc(reviewLabels[review.decision]||review.decision)}</p><small>${review.decision==='buy'?`${review.quantity} units · supplier ${esc(review.supplier_id)} · estimate ${moneyExact(review.estimated_cost_cents/100)}<br>`:''}${esc(review.reason)}</small>`:`<p>Unavailable</p><small>${esc(output.review_error||'A second recommendation could not be prepared.')}</small>`}</div></div>${comparison.differences.length?`<small class="comparison-differences">Differences: ${esc(comparison.differences.map(x=>x.replaceAll('_',' ')).join(', '))}</small>`:''}</section>`:''}
    ${comparison?`<small class="comparison-differences">The cross-check compares the decision and order details. The explanations may use different wording.</small>`:''}
    <details class="agent-explanation"><summary>Why this recommendation?</summary><p>${esc(d.reason)}${output.message!==d.reason?'\n\n'+esc(output.message):''}</p><small class="model-note">Model: ${esc(output.metadata.execution_mode==='live_model'?output.metadata.model:'demo response')}</small></details>
    ${output.error?`<div class="error">${esc(output.error)}</div>`:''}
    ${d.constraints.length?`<div class="constraint-list">${d.constraints.map(c=>`<span class="constraint-chip">${esc(constraintLabel(c))}</span>`).join('')}</div>`:''}
    ${d.rejected_suppliers?.length?`<section class="supplier-alternatives"><div class="section-label">OTHER SUPPLIERS CONSIDERED</div><p class="supplier-intro">These options were not selected for the reason shown.</p>${d.supplier_id?`<article class="supplier-alternative selected-supplier"><strong>Selected supplier · ${esc(d.supplier)}</strong><div>${d.quantity} units · ${money(d.estimated_cost)} · Arrives in ${d.eta_days} days</div></article>`:''}${d.rejected_suppliers.map(s=>`<article class="supplier-alternative"><strong>${esc(s.supplier_name)}</strong><div class="supplier-terms">${s.proposed_quantity} units · ${money(s.estimated_cost)} · Arrives in ${s.eta_days} days</div><p class="supplier-reason">${esc((s.constraints||[]).map(constraintLabel).concat(s.selection_reason?[constraintLabel(s.selection_reason)]:[]).join(' '))}</p></article>`).join('')}</section>`:''}
    ${output.draft?.status==='awaiting_approval'?`<div class="action-row"><div class="approval-intro"><div class="section-label">STEP 3 · YOUR DECISION</div><p>Check the supplier and quantity before approving. Approval places the order.</p></div><div class="action-main"><button class="button primary" id="approve-button">Approve and place order →</button><button class="button secondary" id="reject-button">Decline order</button></div><label class="outcome-label">Demo supplier response <select id="supplier-outcome"><option value="confirmed">Supplier confirms the full order</option><option value="rejected">Supplier declines the order</option><option value="partial">Supplier confirms half the order</option></select><small>This setting simulates how the supplier responds.</small></label></div>`:''}
    ${d.requires_human_approval&&!output.draft?'<div class="validation fail">No draft was prepared. Retry the investigation before any purchase.</div>':''}
    <div id="validation"></div><div class="run-info">Purchasing rules v${esc(d.policy_version)} · Situation version ${d.case_revision} · Completed in ${Number(output.metadata.elapsed_seconds).toFixed(1)} seconds</div>`;
  if($('approve-button')){$('approve-button').onclick=approve;$('reject-button').onclick=rejectDraft;}
  renderTrace(output.trace);
}
async function investigate(){
  if(busy)return;busy=true;$('results').setAttribute('aria-busy','true');$('analyze-button').disabled=true;$('case-select').disabled=true;$('reset-button').disabled=true;$('analyze-button').textContent='Checking situation…';
  try{renderDecision(await request('/api/agent/run',{method:'POST',body:JSON.stringify({case_id:$('case-select').value})}));}
  catch(err){$('empty-state').classList.add('hidden');$('result').classList.remove('hidden');$('result').innerHTML=`<div class="error">${esc(err.message)}</div>`;}
  finally{busy=false;$('results').removeAttribute('aria-busy');$('analyze-button').disabled=false;$('case-select').disabled=false;$('reset-button').disabled=false;$('analyze-button').innerHTML='Review this situation <span>→</span>';refreshEvents();}
}
async function approve(){
  busy=true;$('case-select').disabled=true;$('analyze-button').disabled=true;$('reset-button').disabled=true;
  $('approve-button').disabled=true;$('reject-button').disabled=true;
  try{
    const result=await request(`/api/drafts/${encodeURIComponent(activeResult.draft.id)}/approve`,{method:'POST',body:JSON.stringify({approved:true,outcome:$('supplier-outcome').value})});
    const valid=result.validation.valid;
    $('validation').innerHTML=`<div class="validation ${valid?'ok':'fail'}"><b>${valid?'Validated':'Buyer review required'} · ${esc(result.validation.status)}</b><br>${esc(result.validation.message)}<br>Order ${esc(result.action.id)} · ${result.action.confirmed_quantity}/${result.action.requested_quantity} units confirmed${result.follow_up?`<br>${esc(result.follow_up.message)} Remaining gap: ${result.follow_up.remaining_gap ?? 'unknown'} units.`:''}</div>`;
    cases=await request('/api/cases');renderCaseSummary();
  }catch(err){$('validation').innerHTML=`<div class="validation fail">${esc(err.message)}</div>`;}
  finally{busy=false;$('case-select').disabled=false;$('analyze-button').disabled=false;$('reset-button').disabled=false;refreshEvents();}
}
async function rejectDraft(){
  $('approve-button').disabled=true;$('reject-button').disabled=true;
  try{await request(`/api/drafts/${encodeURIComponent(activeResult.draft.id)}/reject`,{method:'POST'});$('validation').innerHTML='<div class="validation ok">Draft rejected by buyer. No purchase was placed.</div>';}
  catch(err){$('validation').innerHTML=`<div class="validation fail">${esc(err.message)}</div>`;}
  refreshEvents();
}
async function refreshEvents(){
  try{const events=await request(`/api/events?case_id=${encodeURIComponent($('case-select').value||'')}`);
    $('events').className=events.length?'events-list':'events-empty';
    $('events').innerHTML=events.length?events.slice(0,8).map(e=>`<div class="event"><b>${esc(e.type.replaceAll('_',' '))}${e.payload.draft_id?' · '+esc(e.payload.draft_id):''}</b><time>${new Date(e.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</time></div>`).join(''):'No actions recorded for this situation.';
  }catch(err){$('events').textContent=err.message;}
}
async function runEvaluations(){
  $('eval-button').disabled=true;$('eval-button').textContent='Running checks…';
  try{const data=await request('/api/evals');$('eval-results').className='';
    $('eval-results').innerHTML=`<p class="eval-note">Automated checks · ${esc(data.provider)} mode · example set ${esc(data.dataset_version)}</p><table class="eval-table"><thead><tr><th>SITUATION</th><th>DECISION</th><th>CHECKS</th><th>RESULT</th></tr></thead><tbody>${data.results.map(r=>`<tr><td>${esc(r.id)}</td><td>${esc(r.decision)}</td><td>${Object.values(r.checks).filter(Boolean).length}/${Object.keys(r.checks).length}</td><td class="${r.passed?'pass':'fail'}">${r.passed?'PASS':'FAIL'}</td></tr>`).join('')}</tbody></table>`;
  }catch(err){$('eval-results').textContent=err.message;}
  finally{$('eval-button').disabled=false;$('eval-button').textContent='Run checks ↗';}
}
async function resetCase(){
  if(busy)return;busy=true;$('analyze-button').disabled=true;$('case-select').disabled=true;$('reset-button').disabled=true;
  try{await request(`/api/cases/${encodeURIComponent($('case-select').value)}/reset`,{method:'POST'});cases=await request('/api/cases');renderCase();}
  catch(err){$('events').textContent=err.message;}
  finally{busy=false;$('analyze-button').disabled=false;$('case-select').disabled=false;$('reset-button').disabled=false;}
}
async function init(){
  const health=await request('/health');$('environment-label').textContent=`Sample data · ${health.agent_provider==='ollama'?'live model':'demo responses'}`;
  cases=await request('/api/cases');$('case-select').innerHTML=cases.map(c=>`<option value="${esc(c.id)}">${esc(c.title)}</option>`).join('');
  $('case-select').value='recommendation-review';$('case-select').onchange=renderCase;$('analyze-button').onclick=investigate;$('eval-button').onclick=runEvaluations;$('reset-button').onclick=resetCase;renderCase();
}
init().catch(err=>{$('empty-state').innerHTML=`<div class="error">Could not load application: ${esc(err.message)}</div>`;});
