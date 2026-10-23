'use strict';
(() => {
  let state=null, polling=false;
  const note=$('update-message');
  async function refresh(){
    try {
      const response=await fetch('/api/updates/status',{cache:'no-store'});
      if(!response.ok)return;
      state=await response.json();
      $('app-version').textContent=state.current;
      const busy=['checking','downloading','restarting'].includes(state.status);
      $('check-updates').disabled=busy;
      $('install-update').disabled=busy || !state.enabled || state.status!=='ready';
      $('restore-update').disabled=busy || !state.can_restore;
      note.textContent=state.message + (!state.enabled?' In-app installation is available after running the installer on your 64-bit Pi and opening the app from its menu.':'');
      $('release-notes').textContent=state.available?`Version ${state.available.version}\n${state.available.notes}`:'';
      if(busy && !polling)setTimeout(refresh,1000);
    }catch(e){note.textContent='The app is restarting or unavailable. Reopen Thermal Prep if it does not return.';}
  }
  async function action(action){
    if(action!=='check' && busy){note.textContent='Wait for image preparation to finish before updating.';return;}
    if(action!=='check' && !window.confirm('The app will restart and close the current document. Download any prepared copy you want to keep. Printer preferences and pairing are retained. Continue?'))return;
    try {
      const old=await (await fetch('/health',{cache:'no-store'})).json();
      if(action!=='check'){appUpdating=true;setBusy(true);}
      await api('/api/updates/action',JSON.stringify({action}),{'Content-Type':'application/json'});
      if(action==='check'){await refresh();return;}
      polling=true;
      for(const id of ['check-updates','install-update','restore-update'])$(id).disabled=true;
      note.textContent='Preparing update and restarting the app…';
      const deadline=Date.now()+180000;
      while(Date.now()<deadline){
        await new Promise(resolve=>setTimeout(resolve,1000));
        try {
          const health=await (await fetch('/health',{cache:'no-store'})).json();
          if(health.instance!==old.instance){location.reload();return;}
          const status=await (await fetch('/api/updates/status',{cache:'no-store'})).json();
          note.textContent=status.message;
          if(status.status==='failed')break;
        }catch(e){} // The process is briefly absent during restart.
      }
      polling=false;appUpdating=false;setBusy(false);await refresh();
    }catch(e){note.textContent=e.message;polling=false;if(appUpdating){appUpdating=false;setBusy(false);}}
  }
  $('check-updates').addEventListener('click',()=>action('check'));
  $('install-update').addEventListener('click',()=>action('install'));
  $('restore-update').addEventListener('click',()=>action('restore'));
  refresh();
})();
