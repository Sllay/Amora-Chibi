import {AmoraModel,initFramework} from './runtime.js';
const $=id=>document.getElementById(id),canvas=$('avatar');let runtime,identity,restore=null,metricsTimer;
function status(message,error=false){$('status').textContent=message;$('status').dataset.error=String(error);}
function fail(error){runtime?.stop();status(String(error.message||error),true);console.error(error.stack||String(error));window.amoraBootError={stage:runtime?.stage,stack:error.stack||String(error)};}
function controls(){
 const select=$('expression');select.replaceChildren(new Option('Escolher expressão',''));for(const name of runtime.expressions.keys())select.add(new Option(name,name));
 const container=$('parameters');container.replaceChildren();for(const [name,p] of runtime.params){const label=document.createElement('label'),value=document.createElement('output'),slider=document.createElement('input');label.append(name,value,slider);slider.type='range';slider.min=p.min;slider.max=p.max;slider.step=(p.max-p.min)/100||.01;slider.value=p.default;value.value=p.default;slider.oninput=()=>{value.value=slider.value;runtime.setParameters({[name]:Number(slider.value)});};container.append(label);}
}
async function boot(){status('Carregando Cubism e texturas…');if(!identity){const res=await fetch('./identity.json',{cache:'no-cache'});if(!res.ok)throw Error('Missing build identity');identity=await res.json();}
 initFramework();runtime=new AmoraModel(canvas,identity);runtime.setProfile($('profile').value);await runtime.load();if(restore){runtime.setParameters(restore.parameters);if(restore.expression)runtime.setExpression(restore.expression);runtime.setIdle(restore.idle);restore=null;}
 controls();$('identity').textContent='V5.0 · Build '+identity.build+' · MOC3 '+runtime.mocHash.slice(0,12);$('idle').textContent=runtime.idle?'Pausar movimento':'Iniciar movimento';status('Cubism real pronto');runtime.start();clearInterval(metricsTimer);metricsTimer=setInterval(()=>{if(runtime.ready)$('metrics').textContent=JSON.stringify(runtime.diagnostics(),null,2);},1000);
}
$('profile').onchange=()=>runtime?.setProfile($('profile').value);$('expression').onchange=()=>{if(runtime?.ready&&$('expression').value)runtime.setExpression($('expression').value);};$('idle').onclick=()=>{if(!runtime?.ready)return;runtime.setIdle(!runtime.idle);$('idle').textContent=runtime.idle?'Pausar movimento':'Iniciar movimento';};$('reset').onclick=()=>{if(runtime?.ready){runtime.reset();controls();$('idle').textContent='Iniciar movimento';}};
canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();if(runtime?.ready){restore={parameters:Object.fromEntries(runtime.overrides),expression:runtime.selected,idle:runtime.idle};runtime.stop();runtime.ready=false;status('Contexto WebGL perdido; aguardando recuperação.',true);}});
canvas.addEventListener('webglcontextrestored',()=>{try{runtime?.dispose();}catch(e){console.warn('Context release:',e.message);}boot().catch(fail);});
window.addEventListener('pagehide',()=>runtime?.stop());window.addEventListener('pageshow',()=>runtime?.ready&&runtime.start());
Object.defineProperty(window,'AmoraVisualEngineV5',{get:()=>runtime});
boot().catch(fail);
