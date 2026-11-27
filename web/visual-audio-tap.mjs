// Opt-in local playback envelope only. No audio, text, identifiers or additional playback.
export class VisualAudioTap {
 constructor(context){this.context=context;this.node=context.createAnalyser();this.node.fftSize=512;this.node.connect(context.destination);this.samples=new Float32Array(this.node.fftSize);this.channel=null;this.timer=null;}
 enable(){if(this.channel)return;this.channel=new BroadcastChannel('amora.visual.audio-envelope.v1');this.timer=setInterval(()=>this.sample(),50);}
 sample(){if(!this.channel)return;this.node.getFloatTimeDomainData(this.samples);let sum=0;for(const n of this.samples)sum+=n*n;const rms=Math.sqrt(sum/this.samples.length);const level=this.context.state==='running'?Math.max(0,Math.min(1,(rms-.012)*5)):0;this.channel.postMessage({version:1,level,at:Date.now()});}
 disable(){clearInterval(this.timer);this.timer=null;if(this.channel){this.channel.postMessage({version:1,level:0,at:Date.now()});this.channel.close();this.channel=null;}}
}
