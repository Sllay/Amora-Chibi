export const TOKEN_KEY='amora.visual.readonly.v1', AUDIO_CHANNEL='amora.visual.audio-envelope.v1';
const names={neutral:'Neutral',happy:'Happy',sad:'Sad',angry:'Angry',annoyed:'Annoyed',alert:'Neutral',playful:'Happy',curious:'Curious',surprised:'Surprised',shy:'Shy',sleepy:'Sleepy',crying:'Crying',sobbing:'Sobbing',blushing:'Blushing'};
const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
export class BodyController {
 constructor(getRuntime){this.getRuntime=getRuntime;this.state=null;this.session=null;this.sequence=0;this.level=0;this.audioAt=-Infinity;this.last=0;this.runtime=null;this.expression=null;this.values={};}
 accept(m){if(!m||m.version!==1||typeof m.session!=='string'||!Number.isSafeInteger(m.sequence)||m.sequence<1||!Number.isFinite(Date.parse(m.timestamp)))return false;
  if(m.kind==='snapshot'){if(m.sequence!==1||m.session===this.session)return false;}else if(m.session!==this.session||m.sequence<=this.sequence||!['state','heartbeat'].includes(m.kind))return false;
  if(m.kind!=='heartbeat'){const s=m.state;if(!s||!Object.hasOwn(names,s.expression)||typeof s.speaking!=='boolean'||typeof s.blink_allowed!=='boolean'||!['intensity','blush','arousal','valence'].every(k=>Number.isFinite(s[k]))||!(s.intensity>=0&&s.intensity<=1&&s.blush>=0&&s.blush<=1&&Math.abs(s.arousal)<=1&&Math.abs(s.valence)<=1))return false;this.state={...s};}
  this.session=m.session;this.sequence=m.sequence;return true;}
 audio(m,now=performance.now()){if(!m||m.version!==1||!Number.isFinite(m.level)||m.level<0||m.level>1||!Number.isFinite(m.at)||Math.abs(Date.now()-m.at)>600)return false;this.level=m.level;this.audioAt=now;return true;}
 tick(now=performance.now()){const r=this.getRuntime();if(!r?.ready||!this.state)return;const s=this.state;if(r!==this.runtime){this.runtime=r;this.expression=null;this.values={};this.last=now;}
  const dt=clamp((now-this.last)/1000,0,.1);this.last=now;const expression=names[s.expression];if(expression!==this.expression&&r.expressions.has(expression)){r.setExpression(expression);this.expression=expression;}
  const target=now-this.audioAt<=250?this.level:0;
  const gesture=s.gesture==='look_away'?-12:s.gesture==='curious_tilt'?8:s.gesture==='attention'?4:0;
  const targets={ParamMouthOpenY:target,ParamBlush:s.blush,ParamAngleX:gesture};
  for(const [id,value] of Object.entries(targets)){if(!r.params.has(id))continue;const current=this.values[id]??r.readParameters()[id]??0;const tau=id==='ParamMouthOpenY'?(value > current ? .045 : .09):.22;this.values[id]=current+(value-current)*(1-Math.exp(-dt/tau));r.setParameters({[id]:this.values[id]});}
  for(const id of ['ParamEyeLOpen','ParamEyeROpen']){if(!s.blink_allowed&&r.params.has(id))r.setParameters({[id]:1});else r.overrides.delete(id);}
 }
 disconnected(){this.level=0;this.audioAt=-Infinity;}
}
export class VisualConnection{
 constructor(body,onStatus=()=>{}){this.body=body;this.onStatus=onStatus;this.generation=0;this.socket=null;this.timer=null;this.token='';this.attempt=0;}
 connect(token){this.stop();if(!/^browser\.v1\.[A-Za-z0-9_-]+\.[a-f0-9]{64}$/.test(token))throw Error('Credencial visual inválida');this.token=token;this.attempt=0;this.open(++this.generation);}
 open(g){if(g!==this.generation||!this.token)return;const url=new URL('/v1/avatar/visual-stream',location.origin);url.protocol=location.protocol==='https:'?'wss:':'ws:';const ws=new WebSocket(url,['amora.visual.v1','auth.'+this.token]);this.socket=ws;this.onStatus('Conectando');
  ws.onmessage=e=>{if(g!==this.generation||typeof e.data!=='string'||e.data.length>4096)return;try{if(this.body.accept(JSON.parse(e.data))){this.attempt=0;this.onStatus('Core conectado');}}catch(_){}};
  ws.onclose=()=>{if(g!==this.generation)return;this.body.disconnected();this.onStatus('Desconectado');if(++this.attempt<=5)this.timer=setTimeout(()=>this.open(g),Math.min(15000,1000*2**(this.attempt-1)));else this.onStatus('Reconexão esgotada; parear novamente');};
  ws.onerror=()=>this.onStatus('Falha de conexão');}
 stop(){this.generation++;clearTimeout(this.timer);this.socket?.close();this.socket=null;this.token='';this.body.disconnected();}
}
export function attachVisual(getRuntime,{panel=null}={}){
 const body=new BodyController(getRuntime),status=panel?.querySelector('[data-visual-status]'),conn=new VisualConnection(body,s=>{if(status)status.textContent=s;document.body.dataset.coreStatus=s;});
 let channel=null;try{channel=new BroadcastChannel(AUDIO_CHANNEL);channel.onmessage=e=>{if(conn.token&&body.state)body.audio(e.data);};}catch(_){}
 const stored=()=>{try{return localStorage.getItem(TOKEN_KEY)||'';}catch(_){return '';}};
 const connect=()=>{const token=stored();if(token){try{conn.connect(token);}catch(e){if(status)status.textContent=e.message;}}else conn.stop();};
 if(panel){panel.querySelector('form').onsubmit=async e=>{e.preventDefault();const input=panel.querySelector('input'),code=input.value;input.value='';try{const r=await fetch('/v1/pairing/redeem',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,client:'visual'})});if(!r.ok)throw Error('Pareamento recusado');const {token}=await r.json();conn.connect(token);if(panel.querySelector('[data-remember]').checked)localStorage.setItem(TOKEN_KEY,token);else localStorage.removeItem(TOKEN_KEY);if(panel.dataset.overlaySetup==='true'){const url=new URL(location.href);url.searchParams.delete('setup');location.replace(url);}}catch(err){if(status)status.textContent=err.message;}};
 panel.querySelector('[data-unpair]').onclick=()=>{try{localStorage.removeItem(TOKEN_KEY);}catch(_){}conn.stop();if(status)status.textContent='Despareado';};}
 window.addEventListener('storage',e=>{if(e.key===TOKEN_KEY)connect();});window.addEventListener('pagehide',e=>{if(!e.persisted){conn.stop();channel?.close();}});connect();
 return {body,conn,tick:()=>body.tick()};
}
