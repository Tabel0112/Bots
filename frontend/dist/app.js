const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nodes = [
  ['request','Request','Read constraints'], ['plan','ARGUS Plan','Plan bounded work'], ['route','Dispatch','Choose workflow'],
  ['ghost','Ghost v2','Reuse qualified steps'], ['dom','DOM Worker','Browse and extract'], ['gate','Proof sufficient?','Choose verified output or visual recovery'],
  ['visual','Visual Worker','Correct the page when structured proof fails'], ['bundle','Evidence','Collect worker proof'],
  ['moderator','Moderator','Check requirements'], ['result','Result','Five verified records']
];
const graphLayout = {
  request:{x:18,y:87,w:105,h:66}, plan:{x:142,y:87,w:105,h:66}, route:{x:266,y:87,w:105,h:66},
  ghost:{x:390,y:87,w:105,h:66}, dom:{x:514,y:87,w:105,h:66}, gate:{x:638,y:74,w:132,h:92,type:'decision'},
  visual:{x:638,y:228,w:132,h:66}, bundle:{x:789,y:87,w:105,h:66}, moderator:{x:913,y:87,w:105,h:66}, result:{x:1037,y:87,w:105,h:66}
};
const compactGraphLayout = {
  request:{x:25,y:70,w:150,h:60}, plan:{x:220,y:70,w:150,h:60}, route:{x:415,y:70,w:150,h:60},
  ghost:{x:415,y:170,w:150,h:60}, dom:{x:415,y:270,w:150,h:60}, gate:{x:409,y:365,w:162,h:92,type:'decision'},
  visual:{x:145,y:378,w:150,h:66}, bundle:{x:415,y:490,w:150,h:66}, moderator:{x:415,y:590,w:150,h:60}, result:{x:415,y:690,w:150,h:60}
};
const edges = [
  {from:'request',to:'plan'}, {from:'plan',to:'route'}, {from:'route',to:'ghost'}, {from:'ghost',to:'dom'}, {from:'dom',to:'gate'},
  {from:'gate',to:'bundle',kind:'pass'}, {from:'gate',to:'visual',kind:'fallback'}, {from:'visual',to:'dom',kind:'recovery'},
  {from:'bundle',to:'moderator'}, {from:'moderator',to:'result'}
];
const stages = [
  {name:'Plan',x:18,y:17,w:353}, {name:'Execute',x:390,y:17,w:380}, {name:'Verify',x:789,y:17,w:353}
];
const compactStages = [
  {name:'Plan',x:25,y:17,w:540}, {name:'Execute',x:385,y:145,w:190}, {name:'Verify',x:385,y:465,w:190}
];
const edgeLabels = [
  {text:'Proof valid',x:762,y:54}, {text:'Needs visual',x:710,y:184,recovery:true}, {text:'Re-check',x:563,y:239,recovery:true}
];
const compactEdgeLabels = [
  {text:'Proof valid',x:499,y:465}, {text:'Needs visual',x:307,y:386,recovery:true}, {text:'Re-check',x:292,y:298,recovery:true}
];
const products = [['Northline Studio',98],['Soundcore Lite Q30',129],['JBL Tune 770NC',149],['Cedar Audio One',119],['Metro Sound 40',89]];
const labels = {waiting:'Waiting',running:'Running',complete:'Complete',blocked:'Blocked',skipped:'Skipped',cancelled:'Cancelled'};
const scenarios = {success:'Successful reuse',filter:'Filter failure & recovery',offline:'Browser unavailable'};
const runs = [];
let activeRun = null, viewedRun = null, selectedIndex = null, captureMoment = 'after', timer = null, fitted = true, inspectorEvent;
const nodeName = id => nodes.find(n => n[0] === id)?.[1] || id;
const announce = text => { $('#announcement').textContent = text; };
const stamp = ms => new Date(ms).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
const duration = run => ((run?.elapsed || 0) / 1000).toFixed(1) + 's';

function buildGraph() {
  const svg = $('#flowLines'),canvas=$('#flowCanvas');
  stages.forEach((stage,index)=>{
    const label=document.createElement('span');label.className='stage-label';label.textContent=stage.name;
    label.dataset.stageIndex=index;canvas.append(label);
  });
  edges.forEach(({from,to,kind=''}) => {
    const path = document.createElementNS('http://www.w3.org/2000/svg','path');
    path.dataset.from=from;path.dataset.to=to;path.dataset.kind=kind;
    path.classList.add('flow-path');if(kind==='fallback'||kind==='recovery')path.classList.add('fallback');
    svg.append(path);
  });
  edgeLabels.forEach((item,index)=>{
    const label=document.createElement('span');label.className=`flow-label${item.recovery?' branch':''}`;label.textContent=item.text;
    label.dataset.labelIndex=index;canvas.append(label);
  });
  nodes.forEach(([id,title,description],index) => {
    const layout=graphLayout[id],button=document.createElement('button');button.className=`node${layout.type==='decision'?' decision':''}`;button.dataset.node=id;
    Object.assign(button.style,{left:`${layout.x}px`,top:`${layout.y}px`,width:`${layout.w}px`,height:`${layout.h}px`});
    button.innerHTML=`${layout.type==='decision'?'<span class="decision-shape" aria-hidden="true"></span>':''}<span class="node-number">${String(index+1).padStart(2,'0')}</span><strong>${title}</strong><small>${description}</small><span class="node-status">Waiting</span>`;
    button.addEventListener('click',()=>{
      const end=selectedIndex ?? (viewedRun?.events.length-1);
      const event=viewedRun?.events.slice(0,end+1).findLast(e=>e.node===id);
      if(event)selectEvent(event.index);
    });canvas.append(button);
  });
}
function drawGraph() {
  const canvas=$('#flowCanvas'),viewport=$('#graphViewport');if(!canvas.offsetWidth)return;
  const compact=viewport.clientWidth<900,layout=compact?compactGraphLayout:graphLayout,stageLayout=compact?compactStages:stages,labelLayout=compact?compactEdgeLabels:edgeLabels;
  canvas.style.width=compact?'600px':'1160px';canvas.style.height=compact?'780px':'330px';
  $$('[data-node]').forEach(node=>{
    const item=layout[node.dataset.node];Object.assign(node.style,{left:`${item.x}px`,top:`${item.y}px`,width:`${item.w}px`,height:`${item.h}px`});
  });
  $$('.stage-label').forEach(label=>{
    const item=stageLayout[Number(label.dataset.stageIndex)];Object.assign(label.style,{left:`${item.x}px`,top:`${item.y}px`,width:`${item.w}px`});
  });
  $$('.flow-label').forEach(label=>{
    const item=labelLayout[Number(label.dataset.labelIndex)];Object.assign(label.style,{left:`${item.x}px`,top:`${item.y}px`});
  });
  const w=canvas.offsetWidth,h=canvas.offsetHeight;
  $('#flowLines').setAttribute('viewBox',`0 0 ${w} ${h}`);
  $$('.flow-path').forEach(path=>{
    const a=$(`[data-node="${path.dataset.from}"]`),b=$(`[data-node="${path.dataset.to}"]`);
    const ax=a.offsetLeft+a.offsetWidth/2,ay=a.offsetTop+a.offsetHeight/2,bx=b.offsetLeft+b.offsetWidth/2,by=b.offsetTop+b.offsetHeight/2;
    let d;
    if(compact&&path.dataset.kind==='fallback')d=`M${a.offsetLeft},${ay} H${b.offsetLeft+b.offsetWidth+6}`;
    else if(compact&&path.dataset.kind==='recovery')d=`M${ax},${a.offsetTop} V${by} H${b.offsetLeft-6}`;
    else if(path.dataset.kind==='fallback')d=`M${ax},${a.offsetTop+a.offsetHeight} V${b.offsetTop-6}`;
    else if(path.dataset.kind==='recovery')d=`M${a.offsetLeft},${ay} H${bx} V${b.offsetTop+b.offsetHeight+6}`;
    else if(Math.abs(ay-by)<2)d=`M${a.offsetLeft+a.offsetWidth},${ay} H${b.offsetLeft-6}`;
    else d=`M${ax},${a.offsetTop+a.offsetHeight} V${b.offsetTop-6}`;
    path.setAttribute('d',d);
  });
  const canScale=viewport.clientWidth<w,ratio=fitted&&canScale?viewport.clientWidth/w:1;
  canvas.style.transform=`scale(${ratio})`;
  $('#graphSize').style.width=`${w*ratio}px`;$('#graphSize').style.height=`${h*ratio}px`;$('#graphSize').style.marginInline=compact?'auto':'0';
  $('#fitGraph').disabled=!canScale;$('#fitGraph').textContent=!canScale?'Fits view':fitted?'Readable size':'Fit graph';
}
function makeCapture(run,kind,description){
  return{id:`${run.id}-obs-${String(++run.captureCount).padStart(3,'0')}`,kind,description,time:Date.now()};
}
function record(run,node,state,title,detail,options={}){
  const before=run.frame;
  if(options.capture)run.frame=makeCapture(run,options.capture,title);
  run.states[node]=state;
  if(options.checks)run.checks={...run.checks,...options.checks};
  run.events.push({index:run.events.length,node,state,title,detail,time:Date.now(),elapsed:run.elapsed,
    states:{...run.states},checks:{...run.checks},before,after:run.frame,observationCreated:!!options.capture,recovery:!!run.recovered});
  render();announce(title);
}
function block(run,kind,node,title,detail,options={}){
  run.status='blocked';run.blocker=kind;record(run,node,'blocked',title,detail,options);
}
function tick(){
  const run=activeRun;if(!run||run.status!=='running')return;
  run.elapsed+=Date.now()-run.lastTick;run.lastTick=Date.now();
  const step=run.cursor++;
  if(step===0)record(run,'request','complete','Mission received','Five wireless headphones, at most $150 CAD each.');
  if(step===1)record(run,'plan','complete','Plan created','Reuse a matching workflow, inspect the current catalog, and verify all five records.');
  if(step===2)record(run,'route','complete','Qualified workflow selected','Ghost v2 supplies steps to the DOM worker. Visual inspection is reserved for a failed structured check.');
  if(step===3)record(run,'ghost','complete','Ghost v2 bound to this task','Parameters: wireless headphones · 150 CAD · 5 records. This sample match does not skip fresh verification.');
  if(step===4){
    if(run.scenario==='offline'&&!run.recovered){block(run,'offline','dom','Browser session unavailable','The sample browser could not open. No page was captured and no result can be verified.');return;}
    record(run,'dom','running','Catalog opened','Search field found. The requested filter has not been applied yet.',{capture:'search'});
  }
  if(step===5){
    if(run.scenario==='filter'&&!run.recovered){record(run,'dom','complete','Page inspected with a failed filter','The page still shows “Any price”, including a $199 item. Structured proof cannot pass.',{capture:'failed',checks:{query:'passed',price:'failed'}});return;}
    record(run,'dom','complete','Five matching records extracted','The current page shows the query, the $150 CAD filter, and five matching records.',{capture:'filtered',checks:{query:'passed',price:'passed',count:'passed'}});
  }
  if(step===6){
    if(run.scenario==='filter'&&!run.recovered){block(run,'filter','gate','Structured proof insufficient','The failed price check routes this run to visual recovery. No result will be released yet.');return;}
    record(run,'gate','complete','Structured proof sufficient','The current capture proves the query, price limit, and five-record count. Continue to verification.');
  }
  if(step===7)record(run,'visual','skipped','Visual recovery not needed','The proof decision passed, so the optional recovery route was not entered.');
  if(step===8)record(run,'bundle','complete','Evidence bundle assembled','Current observation and five extracted records are ready for independent checks.');
  if(step===9)record(run,'moderator','running','Verifying all five records','Checking query, price, count, and source links against the current sample observation.');
  if(step===10)record(run,'moderator','complete','All requirements passed','Five unique sample catalog links match five current records under $150 CAD.',{checks:{query:'passed',price:'passed',count:'passed',links:'passed'}});
  if(step===11){run.status='completed';record(run,'result','complete','Five verified results ready','All five sample results are available in the inspector.');}
  if(step===21)record(run,'visual','complete','Visual worker confirmed correction','The sample page now shows a $150 CAD maximum and five matching records.',{capture:'filtered',checks:{query:'passed',price:'passed',count:'passed'}});
  if(step===22)record(run,'dom','running','DOM re-check started','Structured extraction is running again against the corrected page.');
  if(step===23)record(run,'dom','complete','Extraction recovered','Fresh records replace the unfiltered attempt. The earlier failed capture remains in history.');
  if(step===24){record(run,'gate','complete','Structured proof now sufficient','The recovery loop returned verified query, price, and count evidence.');run.cursor=8;}
}
function startRun(event){
  event?.preventDefault();
  if(activeRun&&['running','paused','blocked'].includes(activeRun.status))return;
  const run={id:`RUN-${2841+runs.length}`,scenario:$('#scenario').value,status:'running',events:[],states:Object.fromEntries(nodes.map(n=>[n[0],'waiting'])),checks:{query:'pending',price:'pending',count:'pending',links:'pending'},frame:null,captureCount:0,elapsed:0,lastTick:Date.now(),cursor:0,recovered:false};
  runs.unshift(run);activeRun=run;viewedRun=run;selectedIndex=null;captureMoment='after';
  clearInterval(timer);tick();timer=setInterval(tick,1600);render();
}
function selectEvent(index){
  if(!viewedRun?.events[index])return;
  selectedIndex=index;captureMoment='after';render();
}
function setPanel(name){
  $('.workspace').dataset.panel=name;
  $$('.mobile-panels button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.panel===name)));
  requestAnimationFrame(drawGraph);
}
$$('.mobile-panels button').forEach(b=>b.addEventListener('click',()=>setPanel(b.dataset.panel)));
function currentEvent(){return viewedRun?.events[selectedIndex??viewedRun.events.length-1]||null;}
function renderGraph(event){
  const states=event?.states||Object.fromEntries(nodes.map(n=>[n[0],'waiting']));
  $$('[data-node]').forEach(node=>{
    const id=node.dataset.node,state=states[id];
    node.dataset.state=state;node.setAttribute('aria-pressed',String(event?.node===id));
    $('.node-status',node).textContent=state==='waiting'?'Pending':labels[state];
    node.disabled=!viewedRun?.events.slice(0,(event?.index??-1)+1).some(e=>e.node===id);
    node.title=`${nodes.find(n=>n[0]===id)[2]} · ${node.disabled?'No recorded activity yet':'Inspect recorded activity'}`;
    node.setAttribute('aria-label',`${nodeName(id)}: ${labels[state]}`);
  });
  $$('.flow-path').forEach(path=>{
    const a=states[path.dataset.from],b=states[path.dataset.to],kind=path.dataset.kind;let state='waiting';
    if(kind==='pass')state=a==='blocked'?'skipped':a==='complete'?'complete':'waiting';
    else if(kind==='fallback')state=b==='skipped'?'skipped':b==='running'?'running':b==='complete'?'complete':a==='blocked'?'blocked':'waiting';
    else if(kind==='recovery')state=a==='skipped'?'skipped':a==='complete'&&b==='running'?'running':event?.recovery&&a==='complete'&&b==='complete'?'complete':'waiting';
    else if(b!=='waiting')state=b==='running'?'running':b==='blocked'?'blocked':b==='skipped'?'skipped':'complete';
    path.dataset.state=state;
  });
}
function renderTimeline(event){
  const timeline=$('#timeline');$('#eventCount').textContent=viewedRun?.events.length||0;
  if(!viewedRun?.events.length){timeline.innerHTML='<div class="empty"><strong>No activity yet</strong><p>Recorded actions will appear here as the run proceeds.</p></div>';return;}
  // Reuse buttons so arriving events do not destroy keyboard focus.
  if(timeline.dataset.run!==viewedRun.id){timeline.replaceChildren();timeline.dataset.run=viewedRun.id;}
  viewedRun.events.forEach(e=>{
    let button=$(`[data-event="${e.index}"]`,timeline);
    if(!button){
      button=document.createElement('button');button.className='event';button.dataset.event=e.index;
      button.innerHTML=`<time>${stamp(e.time)}</time><span class="event-mark" aria-hidden="true"></span><span class="event-copy"><strong>${escapeHTML(e.title)}</strong><small>${nodeName(e.node)} · ${labels[e.state]}</small></span><span class="event-time">+${(e.elapsed/1000).toFixed(1)}s</span>`;
      button.addEventListener('click',()=>selectEvent(e.index));timeline.append(button);
    }
    button.dataset.state=e.state;button.setAttribute('aria-pressed',String(e.index===event?.index));
    button.setAttribute('aria-label',`Inspect ${e.title}`);
  });
  if(selectedIndex===null)timeline.scrollTop=timeline.scrollHeight;
}
function renderCapture(event){
  const frame=event?.[captureMoment];
  $('#beforeCapture').setAttribute('aria-pressed',String(captureMoment==='before'));
  $('#afterCapture').setAttribute('aria-pressed',String(captureMoment==='after'));
  $('#captureId').textContent=frame?.id.split('-').slice(-2).join('-')||'No capture';
  const region=$('#browserCapture');
  if(!frame){region.innerHTML='<div class="empty"><span class="empty-symbol" aria-hidden="true">▤</span><strong>No browser capture yet</strong><p>Page evidence appears only after the browser opens.</p></div>';$('#captureCaption').textContent='Sample browser captures · no live connection';return;}
  const filtered=frame.kind==='filtered',failed=frame.kind==='failed';
  region.innerHTML=`<div class="browser-bar"><span aria-hidden="true">◉</span><span>catalog.demo / headphones</span><span class="sample-badge">Sample</span></div><div class="catalog"><div class="catalog-heading"><strong>Audio catalog</strong><span>CAD</span></div><div class="catalog-query ${frame.kind==='search'?'highlight':''}">Search: ${frame.kind==='search'?'Not entered':'wireless headphones'}</div><div class="catalog-filter ${failed?'filter-failed':filtered?'highlight':''}">Maximum price <b>${filtered?'$150 CAD':'Any price'}</b></div>${frame.kind==='search'?'<div class="catalog-wait">Enter a query to search the sample catalog.</div>':`<div class="catalog-count">${filtered?'5 matching results':'6 results · filter not applied'}</div>${(failed?[...products,['Premium Studio X',199]]:products).map(([name,price],i)=>`<div class="product ${price>150?'over-budget':''}"><span><strong>${name}</strong><small>Wireless · sample item ${i+1}</small></span><b>$${price}</b></div>`).join('')}`}</div>`;
  $('#captureCaption').textContent=`${stamp(frame.time)} · ${frame.id} · ${event.observationCreated&&captureMoment==='after'?'Captured for this action':'Latest available capture at this moment'}: ${frame.description}.`;
}
const checkInfo={
  query:['Query matches','The search field shows “wireless headphones”.','dom'],
  price:['Price limit applied','The control shows $150 CAD; all five prices are within the limit.','dom'],
  count:['Five records found','Five distinct rows were extracted from the current sample page.','dom'],
  links:['Source links verified','Five sample references correspond to the current observation. These are fixture references, not shopping links.','moderator']
};
function renderInspector(event){
  $('#eventContext').innerHTML=event?`<div class="context-top"><span>${nodeName(event.node)}</span><span class="badge ${event.state}">${labels[event.state]}</span></div><h3>${escapeHTML(event.title)}</h3><p>${escapeHTML(event.detail)}</p>`:'<h3>Select an action to see its proof</h3><p>The map, activity, and evidence follow the same selected moment.</p>';
  renderCapture(event);
  $('#checksList').innerHTML=Object.entries(checkInfo).map(([key,[title,proof]])=>{
    const state=event?.checks[key]||'pending';
    return `<article class="check-card"><div><strong>${title}</strong><span class="badge ${state==='passed'?'complete':state==='failed'?'blocked':''}">${state}</span></div><p>${state==='pending'?'Not checked at this moment.':state==='failed'?'Failed: the page still shows Any price and a $199 result.':proof}</p>${state==='pending'?'':`<button data-proof="${key}">Inspect supporting action →</button>`}</article>`;
  }).join('');
  $$('[data-proof]').forEach(button=>button.addEventListener('click',()=>{
    const key=button.dataset.proof,state=event.checks[key];
    const proof=viewedRun.events.slice(0,event.index+1).findLast(e=>e.checks[key]===state&&(e.observationCreated||e.node===checkInfo[key][2]));
    if(proof){selectEvent(proof.index);setTab('browser');}
  }));
  $('#rawEvidence').textContent=JSON.stringify(event?{mode:'simulation',run:viewedRun.id,event:event.index+1,node:event.node,status:event.state,timestamp:new Date(event.time).toISOString(),checks:event.checks,observation:event.after?.id||null}:{mode:'simulation',status:'waiting'},null,2);
  const complete=event?.states.result==='complete';$('#resultSummary').hidden=!complete;
  $('#resultSummary').innerHTML=complete?`<h3>5 results · all checks passed</h3><p>Sample catalog references for this run.</p><ol>${products.map(([name,price],i)=>`<li><div><strong>${name}</strong><small>catalog.demo/items/${i+1} · fixture</small></div><b>$${price} CAD</b></li>`).join('')}</ol>`:'';
}
function render(){
  const run=viewedRun,event=currentEvent();
  const busy=activeRun&&['running','paused','blocked'].includes(activeRun.status);
  $('#launchRun').disabled=!!busy;$('#scenario').disabled=!!busy;
  $('#launchRun').textContent=busy?'Run in progress':run?'Launch another run':'Launch run';
  $('#runId').textContent=run?.id||'New run';$('#runStatus').textContent=run?.status||'Ready';$('#runStatus').className=`badge ${run?.status==='completed'?'complete':run?.status||''}`;
  $('#runCount').textContent=runs.length;
  const latest=run?.events.at(-1);
  $('#liveAction').textContent=latest?.title||'Ready to explore';
  $('#liveDetail').textContent=run?`${scenarios[run.scenario]} · ${run.status==='completed'?'Run complete':run.status==='cancelled'?'Run ended without a verified result':run.status==='blocked'?'Your action is needed':run.status==='paused'?'Execution paused':'Execution in progress'}`:'Launch a sample run to follow its work.';
  $('#elapsed').textContent=duration(run);
  $('#pauseRun').disabled=run!==activeRun||!['running','paused'].includes(run?.status);
  $('#pauseRun').textContent=run?.status==='paused'?'Resume':'Pause';
  $('#cancelRun').disabled=run!==activeRun||!['running','paused','blocked'].includes(run?.status);
  const history=selectedIndex!==null;$('#selectionBar').classList.toggle('history',history);
  $('#selectionText').textContent=history?`Inspecting ${stamp(event.time)} · event ${event.index+1} of ${run.events.length} · graph and evidence pinned`:run?.status==='completed'?'Completed run · final recorded state':run?.status==='blocked'?'Blocked run · latest recorded state':run?.status==='cancelled'?'Cancelled run · final recorded state':'Live view · '+(event?`event ${event.index+1} · ${run.status}`:'waiting for activity');
  $('#returnLive').hidden=!history;$('#returnLive').textContent=run?.status==='completed'?'Return to latest':'Return to live';
  $('#recovery').hidden=run?.status!=='blocked';
  if(run?.status==='blocked'){
    $('#recoveryTitle').textContent=run.blocker==='filter'?'Stopped: the price limit is not applied':'Stopped: browser unavailable';
    $('#recoveryText').textContent=run.blocker==='filter'?'An over-budget item was caught. Try visual recovery to correct the filter and verify fresh results.':'No browser evidence was captured. Retry with a restored sample session, or cancel this run.';
    $('#recoverRun').textContent=run.blocker==='filter'?'Try visual recovery':'Retry connection';
  }
  renderGraph(event);renderTimeline(event);
  if(inspectorEvent!==event){renderInspector(event);inspectorEvent=event;}else renderCapture(event);
  renderLibraries();requestAnimationFrame(drawGraph);
}
function setTab(name,focus=false){
  ['browser','checks','raw'].forEach(tab=>{const selected=tab===name;$(`#${tab}Tab`).setAttribute('aria-selected',String(selected));$(`#${tab}Tab`).tabIndex=selected?0:-1;$(`#${tab}Panel`).hidden=!selected;});
  if(focus)$(`#${name}Tab`).focus();
}
function renderLibraries(){
  const filter=$('#runFilter').value;
  $('#runsList').innerHTML=runs.filter(r=>filter==='all'||r.status===filter).map(r=>`<button class="record-row" data-open-run="${r.id}"><span><strong>${r.id} · ${scenarios[r.scenario]}</strong><small>${r.events.length} events · ${duration(r)} · ${r.captureCount} sample captures</small></span><span class="badge ${r.status==='completed'?'complete':r.status}">${r.status}</span><span aria-hidden="true">→</span></button>`).join('')||'<div class="empty"><strong>No matching runs</strong><p>Launch a demo from Mission Control to create a run.</p></div>';
  $('#evidenceList').innerHTML=runs.flatMap(r=>r.events.filter(e=>e.observationCreated).map(e=>`<button class="record-row" data-open-run="${r.id}" data-open-event="${e.index}"><span><strong>${e.title}</strong><small>${e.after.id} · ${stamp(e.time)} · sample capture</small></span><span aria-hidden="true">→</span></button>`)).join('')||'<div class="empty"><strong>No captures recorded</strong><p>Evidence appears when a sample browser action produces a capture.</p></div>';
  $$('[data-open-run]').forEach(button=>button.addEventListener('click',()=>{
    viewedRun=runs.find(r=>r.id===button.dataset.openRun);
    selectedIndex=button.dataset.openEvent!==undefined?Number(button.dataset.openEvent):viewedRun.events.length-1;
    captureMoment='after';location.hash='mission';render();
  }));
}
function navigate(){
  const page=['mission','runs','ghost','evidence'].includes(location.hash.slice(1))?location.hash.slice(1):'mission';
  ['mission','runs','ghost','evidence'].forEach(name=>{
    $(`#${name}View`).hidden=page!==name;const link=$(`[data-nav="${name}"]`);
    if(page===name)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
  });
  $('#pageName').textContent={mission:'Mission Control',runs:'Runs',ghost:'Ghost Library',evidence:'Evidence'}[page];
  renderLibraries();requestAnimationFrame(drawGraph);
}
$('#missionForm').addEventListener('submit',startRun);
$('#pauseRun').addEventListener('click',()=>{
  if(viewedRun!==activeRun)return;
  activeRun.status=activeRun.status==='paused'?'running':'paused';activeRun.lastTick=Date.now();render();announce(activeRun.status==='paused'?'Run paused':'Run resumed');
});
$('#cancelRun').addEventListener('click',()=>{
  if(viewedRun!==activeRun)return;activeRun.status='cancelled';
  const node=nodes.find(n=>activeRun.states[n[0]]==='running')?.[0]||'run';record(activeRun,node,'cancelled','Run cancelled','No further actions will run. Existing observations remain available for inspection.');
});
$('#recoverRun').addEventListener('click',()=>{
  const r=viewedRun;if(r!==activeRun||r.status!=='blocked')return;
  r.recovered=true;r.status='running';r.lastTick=Date.now();r.cursor=r.blocker==='filter'?21:5;selectedIndex=null;
  record(r,r.blocker==='filter'?'visual':'dom','running',r.blocker==='filter'?'Visual recovery requested':'Connection retry requested','Retrying this sample run. Earlier events and failed evidence remain available.');
});
$('#returnLive').addEventListener('click',()=>{selectedIndex=null;captureMoment='after';render();});
$('#fitGraph').addEventListener('click',()=>{fitted=!fitted;drawGraph();$('#graphViewport').scrollLeft=0;});
$('#beforeCapture').addEventListener('click',()=>{captureMoment='before';renderCapture(currentEvent());});
$('#afterCapture').addEventListener('click',()=>{captureMoment='after';renderCapture(currentEvent());});
['browser','checks','raw'].forEach((name,index)=>{
  $(`#${name}Tab`).addEventListener('click',()=>setTab(name));
  $(`#${name}Tab`).addEventListener('keydown',e=>{
    const names=['browser','checks','raw'];let next;
    if(e.key==='ArrowRight')next=names[(index+1)%3];if(e.key==='ArrowLeft')next=names[(index+2)%3];if(e.key==='Home')next='browser';if(e.key==='End')next='raw';
    if(next){e.preventDefault();setTab(next,true);}
  });
});
function setTheme(theme){
  document.documentElement.dataset.theme=theme;
  try{localStorage.setItem('argus-demo-theme',theme);}catch{}
  $$('.theme-switch button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.theme===theme)));
}
$$('.theme-switch button').forEach(b=>b.addEventListener('click',()=>setTheme(b.dataset.theme)));
$('#runFilter').addEventListener('change',renderLibraries);
$('#useGhost').addEventListener('click',()=>{if(!activeRun||!['running','paused','blocked'].includes(activeRun.status))$('#scenario').value='success';location.hash='mission';});
window.addEventListener('hashchange',navigate);window.addEventListener('resize',drawGraph);
buildGraph();let theme='paper';try{theme=localStorage.getItem('argus-demo-theme')||'paper';}catch{}
setTheme(theme==='midnight'?'midnight':'paper');render();navigate();
new ResizeObserver(drawGraph).observe($('#graphViewport'));
