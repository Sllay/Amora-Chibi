import {PROFILES} from './runtime.js';
export function readConfig(search){
 const p=new URLSearchParams(search),number=(key,fallback,min,max)=>{const raw=p.get(key);if(raw===null)return fallback;if(!/^\d+(?:\.\d+)?$/.test(raw))throw Error('Invalid '+key);const n=Number(raw);if(!Number.isFinite(n)||n<min||n>max)throw Error('Out of range '+key);return n;};
 const profile=p.get('profile')??'balanced';if(!Object.hasOwn(PROFILES,profile))throw Error('Invalid profile');
 return {profile,scale:number('scale',100,10,100),x:number('x',50,0,100),y:number('y',50,0,100)};
}
export function applyLayout(canvas,c){canvas.style.setProperty('--scale',c.scale+'vmin');canvas.style.setProperty('--x',c.x+'%');canvas.style.setProperty('--y',c.y+'%');}
