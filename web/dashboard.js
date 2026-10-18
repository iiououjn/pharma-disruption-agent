'use strict';
const $ = id => document.getElementById(id);
let bearer = sessionStorage.getItem('pdsia_token') || '';
$('api-token').value = bearer;
function text(node, content){node.textContent = String(content ?? '—');return node;}
function el(tag, className, content){const x=document.createElement(tag); if(className)x.className=className; if(content!==undefined)text(x,content); return x;}
function notice(message, bad=false){text($('notice'),message);$('notice').classList.toggle('error',bad);}
async function request(path, options={}){
  const response = await fetch(path,{...options,headers:{'Authorization':`Bearer ${bearer}`,'Content-Type':'application/json',...(options.headers||{})}});
  if(!response.ok){let detail = response.statusText;try{detail=(await response.json()).detail||detail;}catch{}throw new Error(`${response.status}: ${detail}`);}
  return response.json();
}
function reset(id){const node=$(id);node.replaceChildren();return node;}
function badge(priority){return el('span',`tag ${priority}`,priority);}
function empty(container,label){container.append(el('p','small',label));}
function renderItems(items){const list=reset('items');if(!items.length)empty(list,'No automatically matched active records in this snapshot.');
  for(const it of items){const card=el('article','item');const head=el('div','item-head');head.append(el('h3','',`${it.sku} · ${it.name}`),badge(it.priority));card.append(head,el('p','',it.rationale));
    const stats=el('div','detail-row');for(const [name,value] of [['Stock cover (days)',it.exposure.cover_days],['Lead time (days)',it.exposure.lead_time_days],['FDA records',it.matches.length]]){const x=el('span','small');x.append(el('strong','',value===null?'Unknown':value),document.createTextNode(` ${name}`));stats.append(x);}card.append(stats);
    if(it.missing_info?.length)card.append(el('div','details',`Missing evidence: ${it.missing_info.join('; ')}`));list.append(card);}}
function renderEvents(events){const list=reset('events');if(!events.length)empty(list,'No changes in the compared snapshots.');for(const ev of events){const card=el('div','event');card.append(el('strong','',`${ev.event} · ${ev.record_key}`),el('div','details',ev.generic_name||''));list.append(card);}}
function renderReviews(reviews){const list=reset('review-items');if(!reviews.length)empty(list,'No current candidates awaiting review.');for(const review of reviews){const card=el('article','review');card.append(el('h3','',`${review.record_key} → ${review.sku}`),el('p','small',review.why.join(' · ')));
    const action=el('div','review-actions');for(const decision of ['accept','reject']){const btn=el('button',decision==='reject'?'reject':'',decision==='accept'?'Accept match':'Reject match');btn.addEventListener('click',async()=>{const note=window.prompt(`Reason for ${decision} decision?`);if(note===null)return;btn.disabled=true;try{await request('/reviews',{method:'POST',body:JSON.stringify({record_key:review.record_key,sku:review.sku,decision,note})});notice(`Review saved: ${review.sku} ${decision}. Reloading briefing.`);await refresh();}catch(e){notice(e.message,true);}finally{btn.disabled=false;}});action.append(btn);}card.append(action);list.append(card);}}
function renderHistory(points){const svg=$('history-graph');while(svg.firstChild)svg.firstChild.remove();const width=980,height=250, left=54,bottom=210;svg.setAttribute('viewBox',`0 0 ${width} ${height}`);const create=(tag,attrs)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);svg.append(n);return n;};
  const max=Math.max(1,...points.map(p=>p.record_count));for(let i=0;i<=4;i++){const y=bottom-160*i/4;create('line',{x1:left,y1:y,x2:width-18,y2:y,stroke:'#314458'});const t=create('text',{x:4,y:y+4,fill:'#9fb2c9','font-size':12});t.textContent=String(Math.round(max*i/4));}
  if(points.length){const bw=Math.min(40,(width-left-34)/points.length*.65);points.forEach((p,i)=>{const cx=left+20+(width-left-60)*(i+.5)/points.length,h=160*p.record_count/max;create('rect',{x:cx-bw/2,y:bottom-h,width:bw,height:h,rx:4,fill:'#55dec3'});const t=create('text',{x:cx,y:bottom+22,fill:'#9fb2c9','font-size':11,'text-anchor':'middle'});t.textContent=String(p.snapshot_id);});}
  text($('history-list'),points.map(p=>`#${p.snapshot_id}: ${p.record_count} records (${p.is_synthetic?'SYNTHETIC':'public FDA'})`).join('   ·   '));}
async function refresh(){try{const [b,h]=await Promise.all([request('/briefing'),request('/metrics/history')]);
  for(const key of ['high','medium'])text($(`m-${key}`),b.summary[key]);text($('m-review'),b.summary.needs_review);text($('m-changes'),b.summary.changes);
  text($('snapshot-label'),`Snapshot #${b.snapshot.snapshot_id} · ${b.snapshot.is_synthetic?'SYNTHETIC':'FDA LIVE'}`);
  renderItems(b.items);renderEvents(b.changes);renderReviews(b.review_queue);renderHistory(h);
  text($('connection'),'Connected');notice(`Loaded ${h.length} snapshots. ${b.snapshot.is_synthetic?'All FDA records in this run are synthetic fixtures.':'FDA records are US-only.'} Inventory and catalogue remain simulated.`);
}catch(e){text($('connection'),'Connection error');notice(e.message,true);}}
$('connect').addEventListener('click',()=>{bearer=$('api-token').value.trim();sessionStorage.setItem('pdsia_token',bearer);refresh();});
$('api-token').addEventListener('keydown',e=>{if(e.key==='Enter')$('connect').click();});
for(const tab of document.querySelectorAll('.tab'))tab.addEventListener('click',()=>{for(const t of document.querySelectorAll('.tab'))t.classList.toggle('active',t===tab);for(const p of document.querySelectorAll('.panel'))p.classList.toggle('active',p.id===tab.dataset.tab);});
if(bearer)refresh();
