import {attachVisual} from './body-controller.js';
import {AmoraModel,initFramework} from './runtime.js';
const $=id=>document.getElementById(id),canvas=$('avatar');let runtime,identity,restore=null,metricsTimer;
function status(message,error=false){$('status').textContent=message;$('status').dataset.error=String(error);}
function fail(error){runtime?.stop();status(String(error.message||error),true);console.error(error.stack||String(error));window.amoraBootError={stage:runtime?.stage,stack:error.stack||String(error)};}
function controls(){
 const select=$('expression');select.replaceChildren(new Option('Escolher expressão',''));for(const name of runtime.expressions.keys())select.add(new Option(name,name));
 const container=$('parameters');container.replaceChildren();for(const [name,p] of runtime.params){const label=document.createElement('label'),value=document.createElement('output'),slider=document.createElement('input');label.append(name,value,slider);slider.type='range';slider.min=p.min;slider.max=p.max;slider.step=(p.max-p.min)/100||.01;slider.value=p.default;value.value=p.default;slider.oninput=()=>{value.value=slider.value;runtime.setParameters({[name]:Number(slider.value)});};container.append(label);}
}
const visual=attachVisual(()=>runtime,{panel:$('visual-pair')});
async function boot(){status('Carregando Cubism e texturas…');if(!identity){const res=await fetch('./identity.json',{cache:'no-cache'});if(!res.ok)throw Error('Missing build identity');identity=await res.json();}
 initFramework();runtime=new AmoraModel(canvas,identity);runtime.setProfile($('profile').value);await runtime.load();runtime.beforeFrame=visual.tick;if(restore){runtime.setParameters(restore.parameters);if(restore.expression)runtime.setExpression(restore.expression);runtime.setIdle(restore.idle);restore=null;}
 controls();$('identity').textContent='V5.0 · Build '+identity.build+' · MOC3 '+runtime.mocHash.slice(0,12);$('idle').textContent=runtime.idle?'Pausar movimento':'Iniciar movimento';status('Cubism real pronto');runtime.start();clearInterval(metricsTimer);metricsTimer=setInterval(()=>{if(runtime.ready)$('metrics').textContent=JSON.stringify(runtime.diagnostics(),null,2);},1000);
}
$('profile').onchange=()=>runtime?.setProfile($('profile').value);$('expression').onchange=()=>{if(runtime?.ready&&$('expression').value)runtime.setExpression($('expression').value);};$('idle').onclick=()=>{if(!runtime?.ready)return;runtime.setIdle(!runtime.idle);$('idle').textContent=runtime.idle?'Pausar movimento':'Iniciar movimento';};$('reset').onclick=()=>{if(runtime?.ready){runtime.reset();controls();$('idle').textContent='Iniciar movimento';}};
canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();if(runtime?.ready){restore={parameters:Object.fromEntries(runtime.overrides),expression:runtime.selected,idle:runtime.idle};runtime.stop();runtime.ready=false;status('Contexto WebGL perdido; aguardando recuperação.',true);}});
canvas.addEventListener('webglcontextrestored',()=>{try{runtime?.dispose();}catch(e){console.warn('Context release:',e.message);}boot().catch(fail);});
window.addEventListener('pagehide',()=>runtime?.stop());window.addEventListener('pageshow',()=>runtime?.ready&&runtime.start());
Object.defineProperty(window,'AmoraVisualEngineV5',{get:()=>runtime});
boot().catch(fail);

$('prism-generate').onclick=async()=>{const button=$('prism-generate'),output=$('prism-url'),message=$('prism-status');button.disabled=true;output.value='';$('prism-copy').disabled=true;try{
 const params=new URLSearchParams({profile:$('profile').value});for(const [name,min] of [['scale',10],['x',0],['y',0]]){const field=$('prism-'+name),n=Number(field.value);if(!field.value||!Number.isFinite(n)||n<min||n>100)throw Error('Posição ou tamanho inválido');params.set(name,String(n));}
 const r=await fetch('/avatar/v5/prism-link',{method:'POST',credentials:'same-origin',headers:{'X-Amora-Prism':'1'},cache:'no-store'});if(!r.ok)throw Error('Não foi possível gerar o link ('+r.status+')');const data=await r.json();if(typeof data.path!=='string'||!data.path.startsWith('/avatar/v5/prism/'))throw Error('Resposta inválida');
 const url=new URL(data.path,location.origin);url.search=params.toString();output.value=url.href;$('prism-copy').disabled=false;message.textContent='Link válido por 12 horas. Cole no PRISM.';
 }catch(e){message.textContent=e.message;}finally{button.disabled=false;}};
$('prism-copy').onclick=async()=>{const field=$('prism-url');try{await navigator.clipboard.writeText(field.value);$('prism-status').textContent='Link copiado.';}catch(_){field.focus();field.select();$('prism-status').textContent='Selecione e copie o link.';}};
