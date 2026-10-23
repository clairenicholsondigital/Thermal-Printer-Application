'use strict';
// Printing is always an explicit click. Polling only reads status.
(() => {
  const controls = {address:'printer-address',channel:'printer-channel',media:'printer-media',density:'printer-density',print_mode:'printer-mode',chunk_size:'printer-chunk',pause_ms:'printer-pause'};
  let state = null, initialised = false, pending = false, reachable = false, questionId = null, timer = null, notice = null;
  const operationButtons = ['scan-printers','refresh-printer','pair-printer','connect-printer','disconnect-printer','save-printer','print-test','print-page'];
  const config = () => Object.fromEntries(Object.entries(controls).map(([key,id])=>[key,key==='address'?$(id).value.trim().toUpperCase():Number($(id).value)]));
  const error = text => {$('printer-error').textContent=text;$('printer-error').hidden=!text;};
  const post = (path,data) => api('/api/printer/'+path,JSON.stringify(data),{'Content-Type':'application/json'});
  function requestId() {return Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');}
  function render() {
    const running = appUpdating || pending || !!(state && state.task && state.task.status==='running');
    const connected = reachable && !!(state && state.connection.connected);
    const selected = config();
    $('quality-summary').textContent=`${{1:'Light',8:'Moderate',15:'Dark'}[selected.density]} density (${selected.density}) · ${selected.media===101?'loose A4 sheet':'folded A4 paper'} · settings saved when you connect or print.`;
    const matches = connected && state.connection.address===selected.address && state.connection.channel===selected.channel;
    const ready = reachable && state && state.driver.ready;
    for(const id of operationButtons)$(id).disabled=running || !reachable;
    for(const id of Object.values(controls))$(id).disabled=running;
    $('printer-device').disabled=running;
    $('disconnect-printer').disabled=running || !connected;
    $('connect-printer').disabled=running || !reachable || matches;
    $('print-test').disabled=running || !ready;
    $('connect-printer').textContent=matches?'F11 connected':'Connect F11';
    $('print-page').textContent=running && state && state.task && state.task.kind==='print'?'Printing…':'Print';
    let reason = 'The app connects to the Rongta F11 automatically for each print.';
    if(!reachable)reason='Waiting for the local app. Printing is unavailable until status is known.';
    else if(state && !state.driver.ready)reason=state.driver.message;
    else if(ready && (!job || !result))reason='You can print the test pattern now. Upload a PDF to prepare your own page.';
    else if(ready && (dirty || busy))reason='Update the page preview successfully before printing it.';
    else if(ready && (result.dpi!==203 || result.settings.paper!=='a4'))reason='Select A4 at 203 DPI and update the preview before printing on the F11.';
    else if(ready && !result.black_pixels)reason='The prepared page is blank. Adjust the preview before printing.';
    else if(ready)reason=`Prints selected page ${result.settings.page} once. ${result.orientation==='landscape'?'Landscape artwork is rotated to feed through the A4 print head.':'Load the matching paper before printing.'}`;
    $('print-requirements').textContent=reason;
    $('print-page').disabled=running || !ready || !job || !result || dirty || busy || (result.dpi!==203 || result.settings.paper!=='a4') || !result.black_pixels;
    $('connection-badge').textContent=!reachable?'Status unavailable':connected?'Connected via Bluetooth':'Connects when you print';
    $('connection-badge').classList.toggle('connected',connected);
    if(!state)return;
    $('driver-status').textContent=state.driver.message;
    $('driver-status').classList.toggle('available',state.driver.ready);
    const device=state.discovery.devices.find(d=>d.address.toUpperCase()===selected.address);
    $('paired-status').textContent=device?`${device.name}: ${device.paired?'paired':'not paired'}${device.trusted?', remembered':''}.`:'Your saved F11 address is selected. Connect F11 handles pairing if needed.';
    const task=state.task;
    $('printer-message').textContent=(state.pairing_display || notice || (task && task.message) || 'No printer operation is running.') + (state.connection.last_error?' Connection: '+state.connection.last_error:'');
    $('printer-message').classList.toggle('failed',!notice && !!(task && task.status==='failed'));
    $('transfer-row').hidden=!(task && task.status==='running');
    if(task && task.total){$('transfer-progress').max=task.total;$('transfer-progress').value=task.sent;}
    else $('transfer-progress').removeAttribute('value');
    $('cancel-printer').disabled=pending;
    const q=state.prompt;
    $('pairing-question').hidden=!q;
    if(q){
      if(q.id!==questionId){$('pairing-value').value='';questionId=q.id;}
      $('pairing-message').textContent=q.message;
      $('pairing-value').hidden=q.kind==='confirm';
      $('pairing-value').maxLength=q.kind==='passkey'?6:16;
      $('pairing-value').inputMode=q.kind==='passkey'?'numeric':'text';
      $('pairing-submit').textContent=q.kind==='confirm'?'Confirm code':q.kind==='passkey'?'Submit passkey':'Submit PIN';
    } else {questionId=null;$('pairing-value').value='';}
    $('pairing-submit').disabled=pending;$('pairing-reject').disabled=pending;
    $('print-outcome').hidden=!state.last_print || running;
    const descriptions={unconfirmed:'A completed transfer only means the bytes were sent. Check the actual paper.',visible:'Printed — confirmed by you. Ready for your next page.',blank:'Recorded: blank sheet. Check the thermal side and paper feed, then download diagnostics. The Bluetooth printing issue is still unresolved.', 'no-response':'Recorded: no response. Check paper, printer state and connection, then download diagnostics.',distorted:'Recorded: distorted output. Check paper size and feed settings, then download diagnostics.'};
    if(state.last_print)$('outcome-message').textContent=descriptions[state.last_print.outcome];
    for(const button of document.querySelectorAll('[data-outcome]')){button.disabled=running;button.classList.toggle('selected',!!state.last_print && button.dataset.outcome===state.last_print.outcome);}
  }
  async function refresh() {
    clearTimeout(timer);
    try {
      const response=await fetch('/api/printer/status',{cache:'no-store'});
      if(!response.ok)throw new Error('Printer status could not be read. Reload the app.');
      state=await response.json();reachable=true;
      if(!initialised){for(const [key,id] of Object.entries(controls))$(id).value=state.config[key];initialised=true;}
      const select=$('printer-device'), signature=JSON.stringify(state.discovery.devices);
      if(select.dataset.signature!==signature){
        const previous=select.value;
        select.replaceChildren(new Option('Select a device, or enter its address below',''));
        for(const device of state.discovery.devices)select.add(new Option(`${device.name} · ${device.address}${device.paired?' · paired':''}`,device.address));
        select.value=previous;select.dataset.signature=signature;
      }
    } catch(e) {reachable=false;error(e.message+' No print request will be retried automatically.');}
    render();timer=setTimeout(refresh,state && state.task && state.task.status==='running'?800:3000);
  }
  async function exclusive(work) {
    if(pending)return;
    pending=true;notice=null;error('');render();
    try{await work();}catch(e){error(e.message+' If this happened while starting a print, check its status and the paper before trying again.');}
    finally{pending=false;await refresh();}
  }
  async function action(kind) {
    // Capture the page at the click, before awaiting any network request.
    const payload={action:kind,request_id:requestId()};
    if(kind==='print'){
      if(!job || !result || dirty || busy)return;
      payload.document_id=job.id;payload.result_id=result.base.split('/')[4];
    }
    if(['print','test'].includes(kind) && state && state.task && ['print','test'].includes(state.task.kind) && ['failed','cancelled'].includes(state.task.status)){
      if(!window.confirm('This prints the WHOLE page again; it cannot resume. Let buffered printing finish, switch the F11 off and on, load a fresh A4 sheet and close other printer apps. Ready to print?'))return;
    }
    await exclusive(async()=>{
      if(!['refresh','scan','disconnect'].includes(kind))await post('config',config());
      await post('action',payload);
    });
  }
  for(const [id,kind] of Object.entries({'scan-printers':'scan','refresh-printer':'refresh','pair-printer':'pair','connect-printer':'setup','disconnect-printer':'disconnect','print-test':'test','print-page':'print'}))$(id).addEventListener('click',()=>action(kind));
  $('save-printer').addEventListener('click',()=>exclusive(async()=>{await post('config',config());notice='Printer settings saved.';}));
  $('cancel-printer').addEventListener('click',()=>exclusive(()=>post('cancel',{})));
  async function answer(reject){
    if(!state || !state.prompt)return;
    const data={id:state.prompt.id,value:reject?null:state.prompt.kind==='confirm'?true:$('pairing-value').value};
    $('pairing-value').value='';await exclusive(()=>post('answer',data));
  }
  $('pairing-submit').addEventListener('click',()=>answer(false));$('pairing-reject').addEventListener('click',()=>answer(true));
  $('pairing-value').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();answer(false);}});
  $('printer-device').addEventListener('change',()=>{if($('printer-device').value)$('printer-address').value=$('printer-device').value;render();});
  for(const id of Object.values(controls))$(id).addEventListener('input',render);
  for(const button of document.querySelectorAll('[data-outcome]'))button.addEventListener('click',()=>{if(state && state.last_print)exclusive(()=>post('outcome',{task_id:state.last_print.task_id,outcome:button.dataset.outcome}));});
  document.addEventListener('preview-state',render);
  refresh();
})();
