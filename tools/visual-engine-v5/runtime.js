import {CubismFramework} from './framework/src/live2dcubismframework';
import {CubismUserModel} from './framework/src/model/cubismusermodel';
import {CubismMatrix44} from './framework/src/math/cubismmatrix44';
import {CubismShaderManager_WebGL} from './framework/src/rendering/cubismshader_webgl';
export const PROFILES=Object.freeze({eco:{fps:15,size:256},balanced:{fps:30,size:512},high:{fps:60,size:1024}});
const required=['ParamEyeLOpen','ParamEyeROpen','ParamEyeBallX','ParamEyeBallY','ParamMouthOpenY','ParamMouthPose','ParamBlush','ParamTearL','ParamTearR','ParamAngleX','ParamAngleY','ParamBreath'];
const base=new URL('./model/',import.meta.url), shaderPath=new URL('./shaders/',import.meta.url).href;
function asset(path){if(typeof path!=='string'||!path||path.split('/').some(p=>p==='..'||p==='.')||/[\\:%?#]/.test(path)||path.startsWith('/'))throw Error('Unsafe model reference: '+path);return new URL(path,base);}
async function bytes(url){const r=await fetch(url,{cache:'no-cache'});if(!r.ok)throw Error('HTTP '+r.status+' '+url);return r.arrayBuffer();}
async function json(url){return JSON.parse(new TextDecoder().decode(await bytes(url)));}
async function digest(buffer){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),v=>v.toString(16).padStart(2,'0')).join('');}
function idString(id){const value=id.getString();return typeof value==='string'?value:value.s;}
async function texture(gl,url){
 const b=await bytes(url);const blob=new Blob([b],{type:'image/png'});const image=new Image();const object=URL.createObjectURL(blob);
 try{image.src=object;await image.decode();const t=gl.createTexture();if(!t)throw Error('Texture allocation failed');
 gl.bindTexture(gl.TEXTURE_2D,t);gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,true);
 gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,image);
 gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
 gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);gl.bindTexture(gl.TEXTURE_2D,null);return t;
 }finally{URL.revokeObjectURL(object);}
}
export class AmoraModel extends CubismUserModel {
 constructor(canvas,identity){super();this.canvas=canvas;this.identity=identity;this.gl=null;this.textures=[];this.expressions=new Map();this.motions=new Map();this.params=new Map();this.overrides=new Map();this.profile='balanced';this.idle=true;this.ready=false;this.frames=0;this.frameTimes=[];this.elapsed=0;this.last=0;this.raf=0;this.selected='';}
 async load(){
  if(!globalThis.Live2DCubismCore)throw Error('CUBISM_CORE_MISSING: official development Core required');
  const gl=this.canvas.getContext('webgl2',{alpha:true,premultipliedAlpha:true,antialias:false,preserveDrawingBuffer:false});if(!gl)throw Error('WEBGL2_UNAVAILABLE');this.gl=gl;
  const settings=await json(asset('AmoraBolinha.model3.json'));const refs=settings.FileReferences;
  if(!refs?.Moc||!refs?.Physics||!refs?.Textures?.length||!refs?.Expressions?.length||!refs?.Motions?.Idle?.length)throw Error('Incomplete model references');
  const moc=await bytes(asset(refs.Moc));if(new TextDecoder().decode(moc.slice(0,4))!=='MOC3')throw Error('Invalid MOC3 signature');
  this.mocHash=await digest(moc);if(this.mocHash!==this.identity.mocSha256)throw Error('MOC3 hash mismatch');
  this.stage='moc decode';this.loadModel(moc,true);const model=this.getModel();if(!model)throw Error('CUBISM_MOC_REJECTED');
  this.stage='parameter inventory';for(let i=0;i<model.getParameterCount();i++){const id=idString(model.getParameterId(i));this.params.set(id,{index:i,min:model.getParameterMinimumValue(i),max:model.getParameterMaximumValue(i),default:model.getParameterDefaultValue(i)});}
  for(const name of required)if(!this.params.has(name))throw Error('Missing rig parameter '+name);
  this.stage='physics';const phy=await bytes(asset(refs.Physics));this.loadPhysics(phy,phy.byteLength);if(!this._physics)throw Error('Physics load failed');
  if(refs.Pose){const p=await bytes(asset(refs.Pose));this.loadPose(p,p.byteLength);}
  this.stage='expressions';for(const exp of refs.Expressions){const b=await bytes(asset(exp.File));const e=this.loadExpression(b,b.byteLength,exp.Name);if(!e)throw Error('Expression load failed '+exp.Name);this.expressions.set(exp.Name,e);}
  // CubismMotion starts with null effect arrays; initialize from the model's declared groups before scheduling any motion.
  const effectIds=name=>(settings.Groups??[]).filter(g=>g.Target==='Parameter'&&g.Name===name).flatMap(g=>g.Ids??[]).map(id=>{if(!this.params.has(id))throw Error('Unknown motion effect parameter '+id);return CubismFramework.getIdManager().getId(id);});
  const eyeBlinkIds=effectIds('EyeBlink'),lipSyncIds=effectIds('LipSync');
  this.stage='motions';for(const [group,items] of Object.entries(refs.Motions)){for(const [i,entry] of items.entries()){const b=await bytes(asset(entry.File));const name=group+'/'+i;const m=this.loadMotion(b,b.byteLength,name);if(!m)throw Error('Motion load failed '+name);m.setEffectIds(eyeBlinkIds,lipSyncIds);this.motions.set(name,m);}}
  this.stage='renderer';this.createRenderer(this.canvas.width,this.canvas.height);const renderer=this.getRenderer();renderer.startUp(gl);renderer.setIsPremultipliedAlpha(true);
  // Serial texture upload keeps decoded image memory bounded on mobile.
  this.stage='textures';for(const [i,name] of refs.Textures.entries()){const t=await texture(gl,asset(name));this.textures.push(t);renderer.bindTexture(i,t);}
  this.stage='shaders';renderer.loadShaders(shaderPath);const deadline=performance.now()+20000;
  while(!CubismShaderManager_WebGL.getInstance().getShader(gl)._isShaderLoaded){if(performance.now()>deadline)throw Error('CUBISM_SHADER_TIMEOUT');await new Promise(r=>setTimeout(r,40));}
  this.stage='first frame';this._physics.stabilization(model);this.ready=true;this.reset();this.setIdle(true);this.render(0);return this;
 }
 setProfile(name){const p=PROFILES[name];if(!p)throw Error('Unknown profile');this.profile=name;this.canvas.width=p.size;this.canvas.height=p.size;if(this.ready)this.setRenderTargetSize(p.size,p.size);}
 setParameters(values){for(const [name,value] of Object.entries(values)){const p=this.params.get(name);if(!p||!Number.isFinite(value))throw Error('Invalid parameter '+name);this.overrides.set(name,Math.max(p.min,Math.min(p.max,value)));}}
 setExpression(name){const motion=this.expressions.get(name);if(!motion)throw Error('Unknown expression '+name);this._expressionManager.stopAllMotions();this._expressionManager.startMotion(motion,false);this.selected=name;}
 setIdle(enabled){this.idle=!!enabled;this._motionManager.stopAllMotions();if(this.idle){const motion=this.motions.get('Idle/0');if(motion)this._motionManager.startMotionPriority(motion,false,1);}}
 reset(){this.overrides.clear();this._expressionManager.stopAllMotions();this._motionManager.stopAllMotions();this.selected='';const model=this.getModel();for(const p of this.params.values())model.setParameterValueByIndex(p.index,p.default);model.saveParameters();this.setIdle(false);if(this.ready)this._physics.stabilization(model);}
 render(dt){if(!this.ready||this.gl.isContextLost())return null;const start=performance.now(),gl=this.gl,model=this.getModel();dt=Math.max(0,Math.min(.1,dt));this.elapsed+=dt;this.beforeFrame?.();
  model.loadParameters();if(this.idle){if(this._motionManager.isFinished())this._motionManager.startMotionPriority(this.motions.get('Idle/0'),false,1);this._motionManager.updateMotion(model,dt);}
  this._expressionManager.updateMotion(model,dt);
  for(const [name,value] of this.overrides)model.setParameterValueByIndex(this.params.get(name).index,value);
  this._physics.evaluate(model,dt);if(this._pose)this._pose.updateParameters(model,dt);model.update();
  gl.bindFramebuffer(gl.FRAMEBUFFER,null);gl.viewport(0,0,this.canvas.width,this.canvas.height);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
  const matrix=new CubismMatrix44();const fit=1.8/Math.max(model.getCanvasWidth(),model.getCanvasHeight());matrix.scale(fit,fit);
  const renderer=this.getRenderer();renderer.setMvpMatrix(matrix);renderer.setRenderState(null,[0,0,this.canvas.width,this.canvas.height]);renderer.drawModel(shaderPath);
  this.frames++;this.frameTimes.push(performance.now()-start);if(this.frameTimes.length>240)this.frameTimes.shift();return gl;
 }
 start(){if(this.raf)return;this.last=0;const tick=(now)=>{this.raf=requestAnimationFrame(tick);if((document.hidden&&!this.allowHidden?.())||!this.ready){this.last=0;return;}const interval=1000/PROFILES[this.profile].fps;if(!this.last||now-this.last>=interval-1){const dt=this.last?(now-this.last)/1000:1/PROFILES[this.profile].fps;this.last=now;this.render(dt);}};this.raf=requestAnimationFrame(tick);}
 stop(){cancelAnimationFrame(this.raf);this.raf=0;}
 diagnostics(){const m=this.getModel(),times=[...this.frameTimes].sort((a,b)=>a-b);return {engine:'Cubism Web 5-r.5',build:this.identity.build,mocSha256:this.mocHash,coreVersion:globalThis.Live2DCubismCore.Version.csmGetVersion(),drawables:m.getDrawableCount(),maskedDrawables:Array.from(m.getDrawableMaskCounts()).filter(n=>n>0).length,parameters:this.params.size,textures:this.textures.length,expressions:[...this.expressions.keys()],motions:[...this.motions.keys()],physics:!!this._physics,profile:this.profile,frames:this.frames,renderCpuP95Ms:times[Math.floor((times.length-1)*.95)]??0};}
 readParameters(){return Object.fromEntries([...this.params].map(([name,p])=>[name,this.getModel().getParameterValueByIndex(p.index)]));}
 dispose(){this.stop();this.ready=false;if(this.gl&&!this.gl.isContextLost())for(const t of this.textures)this.gl.deleteTexture(t);this.textures=[];super.release();}
}
export function initFramework(){if(!CubismFramework.isInitialized()){if(!CubismFramework.startUp())throw Error('Cubism startup failed');CubismFramework.initialize();}}
