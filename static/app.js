'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="thermal-token"]').content;
let job = null, result = null, busy = false, dirty = false, zoom = 0;
const fields = ['paper','dpi','orientation','rotation','crop','margin','mode','threshold','brightness','contrast','coloured_lines','sharpen'];
function message(text, spinning=false) { $('status').textContent=text; $('status').classList.toggle('busy',spinning); }
function fail(error) { $('error').textContent=error.message || String(error); $('error').hidden=false; }
function clearError() { $('error').hidden=true; }
function downloads(enabled) {
  document.dispatchEvent(new Event('preview-state'));
  for (const id of ['download-png','download-pdf']) { const el=$(id); el.classList.toggle('disabled',!enabled); el.setAttribute('aria-disabled',String(!enabled)); el.tabIndex=enabled?0:-1; if(!enabled)el.removeAttribute('href'); }
  if(enabled && result) { $('download-png').href=result.base+'thermal.png?download=1'; $('download-pdf').href=result.base+'thermal.pdf?download=1'; $('download-png').download='thermal-prepared.png'; $('download-pdf').download='thermal-prepared.pdf'; }
}
function setBusy(value) { busy=value; $('settings').disabled=value || !job; $('file').disabled=value; $('clear').disabled=value; $('page').disabled=value; $('convert').textContent=value?'Preparing preview…':'Update preview ↗'; document.dispatchEvent(new Event('preview-state')); }
function settings() {
  return {page:Number($('page').value),paper:$('paper').value,dpi:Number($('dpi').value),orientation:$('orientation').value,rotation:Number($('rotation').value),crop:$('crop').checked,margin:Number($('margin').value),mode:$('mode').value,threshold:Number($('threshold').value),brightness:Number($('brightness').value),contrast:Number($('contrast').value),coloured_lines:$('coloured_lines').checked,sharpen:$('sharpen').checked};
}
function updateLabels() { for(const name of ['brightness','contrast'])$(name+'-value').value=Number($(name).value).toFixed(2); $('threshold-value').value=$('threshold').value; $('threshold-control').hidden=$('mode').value!=='threshold'; }
function markDirty() { updateLabels(); if(!job || busy)return; dirty=true; downloads(false); message('Settings changed. Update the preview to apply them.'); $('download-note').textContent='Update the preview before downloading your changes.'; }
async function api(path, body, extra={}) {
  const response=await fetch(path,{method:'POST',headers:{'X-Thermal-Token':token,...extra},body});
  let data; try {data=await response.json();} catch {throw new Error('The app did not respond. Check that Thermal Prep is still running.');}
  if(!response.ok)throw new Error(data.error || 'The request failed.'); return data;
}
async function upload(file) {
  if(!file || busy)return;
  clearError();
  if(!file.name.toLowerCase().endsWith('.pdf'))return fail(new Error('Choose a PDF file.'));
  if(file.size>25*1024*1024 || !file.size)return fail(new Error('Choose a non-empty PDF under 25 MB.'));
  setBusy(true); downloads(false); message('Opening your PDF…',true);
  try {
    job=await api('/api/upload',file,{'Content-Type':'application/pdf','X-Filename':encodeURIComponent(file.name)});
    result=null; $('comparison').hidden=true; $('empty').hidden=false;
    $('filename').textContent=job.name; $('document').hidden=false; $('dropzone').hidden=true;
    $('page').replaceChildren();
    for(let i=1;i<=job.pages;i++){const option=document.createElement('option');option.value=i;option.textContent=`${i} of ${job.pages}`;$('page').append(option);}
    $('page-note').textContent=job.pages===1?'One-page PDF.':'Choose a page. Downloads contain that page only.';
    setBusy(false); await convert();
  } catch(error) { fail(error); message('Could not open this PDF.'); setBusy(false); downloads(!!result && !dirty); }
  finally {$('file').value='';}
}
function imageLoaded(element, url) {return new Promise((resolve,reject)=>{element.onload=resolve;element.onerror=()=>reject(new Error('The preview could not be loaded. Update it to try again.'));element.src=url;});}
async function convert() {
  if(!job || busy)return;
  if(!$('margin').checkValidity()){$('margin').reportValidity();return;}
  clearError(); setBusy(true); downloads(false); message('Rendering your page and preparing its pixels…',true);
  try {
    const data=await api('/api/convert',JSON.stringify({id:job.id,settings:settings()}),{'Content-Type':'application/json'});
    await Promise.all([imageLoaded($('original'),data.base+'original.png'),imageLoaded($('prepared'),data.base+'thermal.png')]);
    result=data; dirty=false; $('empty').hidden=true; $('comparison').hidden=false;
    $('dimensions').textContent=`${data.width.toLocaleString()} × ${data.height.toLocaleString()} px · ${data.dpi} DPI`;
    $('paper-info').textContent=`${data.settings.paper==='a4'?'A4':'Letter'} · ${data.orientation}`;
    $('coverage').textContent=`${data.coverage.toFixed(2)}%`;
    $('prepared-label').textContent=data.settings.mode==='dither'?'Detail-preserving dots':'Solid black and white';
    $('warnings').replaceChildren();
    for(const warning of data.warnings){const p=document.createElement('p');p.textContent=warning;$('warnings').append(p);}
    $('warnings').hidden=!data.warnings.length;
    $('download-note').textContent=`Selected page ${data.settings.page}. Both downloads use the pixels shown above.`;
    for(const id of ['fit','actual','double'])$(id).disabled=false;
    applyZoom(zoom); downloads(true); message(data.black_pixels?'Preview ready. Zoom in to inspect small details.':'Preview ready, but the output contains no black pixels.');
  } catch(error){dirty=true;fail(error);message('Preview could not be updated.');$('download-note').textContent='Update the preview successfully before downloading.';}
  finally{setBusy(false);}
}
function applyZoom(value) {
  zoom=value;const im=$('prepared');$('output-well').classList.toggle('zoomed',!!value);
  if(value && result){im.style.width=(result.width*value)+'px';im.style.height=(result.height*value)+'px';}
  else {im.style.removeProperty('width');im.style.removeProperty('height');}
  for(const [id,v] of [['fit',0],['actual',1],['double',2]]){$(id).classList.toggle('active',value===v);$(id).setAttribute('aria-pressed',String(value===v));}
}
async function reset() {
  if(busy)return; clearError(); setBusy(true);
  try {await api('/api/reset','');job=null;result=null;dirty=false;downloads(false);$('document').hidden=true;$('dropzone').hidden=false;$('comparison').hidden=true;$('empty').hidden=false;$('original').removeAttribute('src');$('prepared').removeAttribute('src');for(const id of ['fit','actual','double'])$(id).disabled=true;message('Choose a PDF to get started.');$('download-note').textContent='Downloads become available after conversion.';}catch(error){fail(error);}finally{setBusy(false);}
}
$('file').addEventListener('change',event=>upload(event.target.files[0]));
$('clear').addEventListener('click',reset);$('convert').addEventListener('click',convert);
$('page').addEventListener('change',()=>{markDirty();convert();});
for(const id of fields)$(id).addEventListener('input',markDirty);
$('preset').addEventListener('change',()=>{
 const type=$('preset').value;$('mode').value=type==='text'?'threshold':'dither';$('threshold').value=170;$('brightness').value=1;$('contrast').value=1;$('coloured_lines').checked=false;$('sharpen').checked=type!=='photo';
 $('preset-hint').textContent={diagram:'Preserve component detail with small black dots.',text:'Crisp lettering and lines. Inspect pale content carefully.',photo:'Keep tonal shading with a pattern of small dots.'}[type];markDirty();
});
for(const [id,v] of [['fit',0],['actual',1],['double',2]])$(id).addEventListener('click',()=>applyZoom(v));
for(const event of ['dragenter','dragover'])$('dropzone').addEventListener(event,e=>{e.preventDefault();if(!busy)$('dropzone').classList.add('dragging');});
for(const event of ['dragleave','drop'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.remove('dragging');});
$('dropzone').addEventListener('drop',e=>upload(e.dataTransfer.files[0]));
// Prevent an accidental drop outside the target from navigating away from the app.
document.addEventListener('dragover',e=>e.preventDefault());document.addEventListener('drop',e=>e.preventDefault());
downloads(false);updateLabels();
