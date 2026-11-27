import {attachVisual} from './body-controller.js';
import {AmoraModel,initFramework,PROFILES} from './runtime.js';
import {readConfig,applyLayout} from './presentation-config.js';
import {CompanionVideo} from './companion-video.js';
const mode=document.body.dataset.mode,canvas=document.querySelector('#avatar'),status=document.querySelector('#status');let runtime,identity,config,restore,booting=false,disposed=false,video;
const setStatus=(message,state)=>{document.body.dataset.state=state;if(status)status.textContent=message;};
const fail=e=>{runtime?.stop();setStatus(String(e.message||e),'error');console.error(e.stack||String(e));};
if(mode==='companion'){
 const media=document.querySelector('#preview'),message=document.querySelector('#pip-status');
 video=new CompanionVideo(canvas,media,d=>{message.textContent=d.state+(d.failure?' — '+d.failure:'');document.querySelector('#enter-pip').disabled=!video?.stream||d.active;document.querySelector('#exit-pip').disabled=!d.active;});
 document.querySelector('#prepare-pip').onclick=()=>{if(runtime?.ready)video.prepare(PROFILES[runtime.profile].fps);};
 document.querySelector('#enter-pip').onclick=()=>video.enter();
 document.querySelector('#exit-pip').onclick=()=>video.exit();
 document.querySelector('#stop-pip').onclick=async()=>{await video.exit();video.stop();};
}
// Opt-in setup inside an isolated browser-source profile; no credential in URL.
if(mode==='overlay'&&!location.pathname.startsWith('/avatar/v5/prism/')&&new URLSearchParams(location.search).get('setup')==='1'){
 const panel=document.createElement('section');panel.id='visual-pair';panel.dataset.overlaySetup='true';
 panel.innerHTML='<h2>Parear fonte de live</h2><form><label>Código <input inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autocomplete="off"></label><label><input type="checkbox" data-remember checked required> Salvar credencial visual por 1 hora</label><button type="submit">Parear</button></form><button type="button" data-unpair>Desparear</button><p data-visual-status role="status">Desconectado</p>';
 document.body.append(panel);
}
const prism=mode==='overlay'&&location.pathname.startsWith('/avatar/v5/prism/');
const visual=attachVisual(()=>runtime,{panel:prism?null:document.querySelector('#visual-pair'),isolated:prism});
let prismTimer,prismStopped=false;
async function refreshPrism(){if(prismStopped)return;try{
 const response=await fetch(new URL('./session',import.meta.url),{cache:'no-store',credentials:'omit'});if(!response.ok){visual.conn.stop();document.body.dataset.coreStatus='Link expirado ou indisponível';if(response.status>=500)prismTimer=setTimeout(refreshPrism,30000);return;}
 const data=await response.json();if(prismStopped)return;visual.conn.connect(data.token);prismTimer=setTimeout(refreshPrism,Math.max(1000,(data.expires_in>120?data.expires_in-60:data.expires_in)*1000));
 }catch(_){if(!prismStopped)prismTimer=setTimeout(refreshPrism,30000);}}
if(prism){refreshPrism();window.addEventListener('pagehide',e=>{if(!e.persisted){prismStopped=true;clearTimeout(prismTimer);}});}

async function boot(){if(booting||disposed)return;booting=true;try{
 setStatus('Carregando Cubism','loading');if(!config){config=readConfig(location.search);applyLayout(canvas,config);}
 if(!identity){const r=await fetch(new URL('./identity.json',import.meta.url),{cache:'no-cache'});if(!r.ok)throw Error('Missing identity');identity=await r.json();}
 initFramework();runtime=new AmoraModel(canvas,identity);runtime.setProfile(config.profile);runtime.allowHidden=()=>!!video?.active;await runtime.load();runtime.beforeFrame=visual.tick;
 if(disposed){runtime.dispose();return;}
 if(restore){runtime.setParameters(restore.parameters);if(restore.expression)runtime.setExpression(restore.expression);runtime.setIdle(restore.idle);restore=null;}
 if(mode==='companion'){const label=document.querySelector('#identity');label.textContent=identity.build.slice(0,12)+' · MOC3 '+runtime.mocHash.slice(0,12);document.querySelector('#prepare-pip').disabled=false;}
 setStatus('Cubism real pronto','ready');runtime.start();
 }catch(e){fail(e);}finally{booting=false;}}
canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();if(runtime?.ready){restore={parameters:Object.fromEntries(runtime.overrides),expression:runtime.selected,idle:runtime.idle};runtime.stop();runtime.ready=false;}setStatus('Contexto WebGL perdido','context-lost');});
canvas.addEventListener('webglcontextrestored',()=>{try{runtime?.dispose();}catch(e){console.warn(e.message);}boot();});
window.addEventListener('pagehide',e=>{runtime?.stop();if(!e.persisted){disposed=true;video?.stop();if(!booting)runtime?.dispose();}});
window.addEventListener('pageshow',()=>{if(runtime?.ready)runtime.start();});
document.addEventListener('visibilitychange',()=>{if(runtime?.ready&&!document.hidden)runtime.start();if(document.hidden&&video?.active&&status)status.textContent='PiP solicitado; continuidade em segundo plano depende do sistema.';});
Object.defineProperty(window,'AmoraPresentation',{value:{get runtime(){return runtime;},get config(){return config;},get video(){return video;},get mode(){return mode;}}});
boot();
