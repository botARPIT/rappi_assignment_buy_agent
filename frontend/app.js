const $ = id => document.getElementById(id);
let cases = [];
let activeResult = null;
let busy = false;
const money = value => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(value || 0);
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function request(url, options={}) {
  const response=await fetch(url,{headers:{'Content-Type':'application/json'},...options});
  const data=await response.json();
  if(!response.ok) throw new Error(typeof data.error==='string'?data.error:JSON.stringify(data.detail || data));
  return data;
}
const selectedCase=()=>cases.find(c=>c.id===$('case-select').value);
function renderCase() {
  const c=selectedCase();if(!c)return;
  const feature={recommendation_review:'Recommendation review',supplier_shortfall:'Supplier shortfall',demand_change:'Demand change'}[c.feature];
  $('case-summary').innerHTML=`<div class="case-title">${esc(c.product)}</div><span class="case-tag">${esc(feature)}</span><div class="facts">
    <div class="fact"><small>FULFILLMENT NODE</small><b>${esc(c.node)}</b></div><div class="fact"><small>ON HAND</small><b>${c.inventory.on_hand} units</b></div>
    <div class="fact"><small>ORDER / CONFIRMED</small><b>${c.purchase_order.ordered_quantity} / ${c.purchase_order.confirmed_quantity}</b></div><div class="fact"><small>ORDER ARRIVAL</small><b>Day ${c.purchase_order.eta_days}</b></div>
    <div class="fact"><small>FORECAST / ACTUAL</small><b>${c.demand.forecast_daily} / ${c.demand.recent_daily} per day</b></div><div class="fact"><small>SALES EVIDENCE</small><b>${c.demand.observed_days} days · ${c.demand.age_hours}h old</b></div>
    <div class="fact"><small>BUDGET REMAINING</small><b>${money(c.budget_cents/100)}</b></div><div class="fact"><small>STORAGE CAPACITY</small><b>${c.storage_capacity} units</b></div>
    ${c.recommended_quantity!==null?`<div class="fact"><small>SYSTEM RECOMMENDATION</small><b>${c.recommended_quantity} units</b></div>`:''}
    ${c.additional_incoming.length?`<div class="fact"><small>ADDITIONAL CONFIRMED</small><b>${c.additional_incoming.reduce((sum,r)=>sum+r.quantity,0)} units</b></div>`:''}</div>`;
  activeResult=null;$('result').classList.add('hidden');$('empty-state').classList.remove('hidden');
  $('trace').className='trace-empty';$('trace').textContent='Run an investigation to see which systems the agent consulted.';
  $('trace-count').textContent='0 calls';refreshEvents();
}
function renderTrace(trace) {
  $('trace-count').textContent=`${trace.length} calls`;$('trace').className='trace-list';
  $('trace').innerHTML=trace.map((step,i)=>`<div class="trace-item"><span class="trace-number">${String(i+1).padStart(2,'0')}</span><details><summary>${esc(step.tool)}</summary><pre>${esc(JSON.stringify({input:step.input,result:step.result},null,2))}</pre></details></div>`).join('');
}
function renderDecision(output) {
  activeResult=output;const d=output.decision,e=d.evidence;
  const labels={accept:'Accept the purchase recommendation',modify:'Modify the purchase recommendation',reject:'Reject unnecessary buying',investigate:'More evidence is needed',supplemental_purchase:'Prepare a supplemental purchase',adjust_plan:'Adjust the purchasing plan',no_action:'No additional purchase needed',escalate:'Buyer intervention is needed'};
  const metrics=[['Demand + safety target',`${e.target_units_through_replenishment ?? '—'} units`],['Available supply',`${e.available_units_on_hand_plus_confirmed ?? '—'} units`],['Coverage gap',`${e.uncovered_units ?? '—'} units`],['Proposed order',d.quantity?`${d.quantity} units · ${money(d.estimated_cost)}`:'No order']];
  $('empty-state').classList.add('hidden');$('result').classList.remove('hidden');
  $('result').innerHTML=`<div class="result-header"><div><div class="section-label">POLICY-VERIFIED RECOMMENDATION</div><div class="result-title">${esc(labels[d.decision]||d.decision)}</div><div class="result-copy">${esc(d.reason)}</div></div><span class="badge ${d.requires_human_approval?'green':d.decision==='escalate'?'amber':'gray'}">${esc(d.decision.replaceAll('_',' '))}</span></div>
    <div class="evidence-grid">${metrics.map(([label,value])=>`<div class="metric"><small>${esc(label)}</small><b>${esc(value)}</b></div>`).join('')}</div>
    <details class="agent-explanation"><summary>Agent explanation · ${esc(output.metadata.execution_mode==='live_model'?output.metadata.model:'scripted test model')}</summary><p>${esc(output.message)}</p></details>
    ${output.error?`<div class="error">${esc(output.error)}</div>`:''}
    ${d.constraints.length?`<div class="constraint-list">${d.constraints.map(c=>`<span class="constraint-chip">${esc(c.replaceAll('_',' '))}</span>`).join('')}</div>`:''}
    ${output.draft?.status==='awaiting_approval'?`<div class="action-row"><div class="action-main"><button class="button primary" id="approve-button">Approve &amp; place order →</button><button class="button secondary" id="reject-button">Reject draft</button></div><label class="outcome-label">Mock supplier response<select id="supplier-outcome"><option value="confirmed">Confirm full quantity</option><option value="rejected">Reject the order</option><option value="partial">Confirm half the quantity</option></select></label></div>`:''}
    ${d.requires_human_approval&&!output.draft?'<div class="validation fail">No draft was prepared. Retry the investigation before any purchase.</div>':''}
    <div id="validation"></div><div class="run-info">Policy ${esc(d.policy_version)} · Revision ${d.case_revision} · ${output.metadata.elapsed_seconds}s</div>`;
  if($('approve-button')){$('approve-button').onclick=approve;$('reject-button').onclick=rejectDraft;}
  renderTrace(output.trace);
}
async function investigate(){
  if(busy)return;busy=true;$('analyze-button').disabled=true;$('case-select').disabled=true;$('reset-button').disabled=true;$('analyze-button').textContent='Investigating…';
  try{renderDecision(await request('/api/agent/run',{method:'POST',body:JSON.stringify({case_id:$('case-select').value})}));}
  catch(err){$('empty-state').classList.add('hidden');$('result').classList.remove('hidden');$('result').innerHTML=`<div class="error">${esc(err.message)}</div>`;}
  finally{busy=false;$('analyze-button').disabled=false;$('case-select').disabled=false;$('reset-button').disabled=false;$('analyze-button').innerHTML='Investigate situation <span>→</span>';refreshEvents();}
}
async function approve(){
  busy=true;$('case-select').disabled=true;$('analyze-button').disabled=true;$('reset-button').disabled=true;
  $('approve-button').disabled=true;$('reject-button').disabled=true;
  try{
    const result=await request(`/api/drafts/${encodeURIComponent(activeResult.draft.id)}/approve`,{method:'POST',body:JSON.stringify({approved:true,outcome:$('supplier-outcome').value})});
    const valid=result.validation.valid;
    $('validation').innerHTML=`<div class="validation ${valid?'ok':'fail'}"><b>${valid?'Validated':'Buyer review required'} · ${esc(result.validation.status)}</b><br>${esc(result.validation.message)}<br>Order ${esc(result.action.id)} · ${result.action.confirmed_quantity}/${result.action.requested_quantity} units confirmed${result.follow_up?`<br>${esc(result.follow_up.message)} Remaining gap: ${result.follow_up.remaining_gap ?? 'unknown'} units.`:''}</div>`;
    cases=await request('/api/cases');
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
  $('eval-button').disabled=true;$('eval-button').textContent='Running DeepEval…';
  try{const data=await request('/api/evals');$('eval-results').className='';
    $('eval-results').innerHTML=`<p class="eval-note">DeepEval · ${esc(data.provider)} agent mode · dataset ${esc(data.dataset_version)}</p><table class="eval-table"><thead><tr><th>SCENARIO</th><th>DECISION</th><th>CHECKS</th><th>RESULT</th></tr></thead><tbody>${data.results.map(r=>`<tr><td>${esc(r.id)}</td><td>${esc(r.decision)}</td><td>${Object.values(r.checks).filter(Boolean).length}/${Object.keys(r.checks).length}</td><td class="${r.passed?'pass':'fail'}">${r.passed?'PASS':'FAIL'}</td></tr>`).join('')}</tbody></table>`;
  }catch(err){$('eval-results').textContent=err.message;}
  finally{$('eval-button').disabled=false;$('eval-button').textContent='Run DeepEval ↗';}
}
async function resetCase(){
  if(busy)return;busy=true;$('analyze-button').disabled=true;$('case-select').disabled=true;$('reset-button').disabled=true;
  try{await request(`/api/cases/${encodeURIComponent($('case-select').value)}/reset`,{method:'POST'});cases=await request('/api/cases');renderCase();}
  catch(err){$('events').textContent=err.message;}
  finally{busy=false;$('analyze-button').disabled=false;$('case-select').disabled=false;$('reset-button').disabled=false;}
}
async function init(){
  const health=await request('/health');$('environment-label').textContent=`Mock purchasing · ${health.agent_provider==='ollama'?'live Ollama agent':'scripted test agent'}`;
  cases=await request('/api/cases');$('case-select').innerHTML=cases.map(c=>`<option value="${esc(c.id)}">${esc(c.title)}</option>`).join('');
  $('case-select').value='recommendation-review';$('case-select').onchange=renderCase;$('analyze-button').onclick=investigate;$('eval-button').onclick=runEvaluations;$('reset-button').onclick=resetCase;renderCase();
}
init().catch(err=>{$('empty-state').innerHTML=`<div class="error">Could not load application: ${esc(err.message)}</div>`;});
