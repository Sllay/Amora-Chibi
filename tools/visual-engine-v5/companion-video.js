// Real animated canvas -> local MediaStream -> HTMLVideoElement -> system PiP.
export class CompanionVideo {
 constructor(canvas,video,onStatus){this.canvas=canvas;this.video=video;this.onStatus=onStatus;this.stream=null;this.frames=0;this.lastFrame=0;this.callback=null;this.active=false;this.state='idle';this.failure='';this.generation=0;
  this.sync=()=>{this.active=document.pictureInPictureElement===video||video.webkitPresentationMode==='picture-in-picture';this.report(this.active?'pip-active':this.stream?'preview':'idle');};
  for(const event of ['enterpictureinpicture','leavepictureinpicture','webkitpresentationmodechanged'])video.addEventListener(event,this.sync);
 }
 report(state,failure=''){this.state=state;this.failure=failure;this.onStatus?.(this.diagnostics());}
 diagnostics(){return {state:this.state,failure:this.failure,active:this.active,frames:this.frames,lastFrame:this.lastFrame,track:this.stream?.getVideoTracks()[0]?.readyState??'none',standard:!!document.pictureInPictureEnabled&&typeof this.video.requestPictureInPicture==='function',webkit:typeof this.video.webkitSetPresentationMode==='function'&&!!this.video.webkitSupportsPresentationMode?.('picture-in-picture'),capture:typeof this.canvas.captureStream==='function'};}
 async prepare(fps){if(this.stream)return;if(typeof this.canvas.captureStream!=='function'){this.report('unsupported','Canvas capture indisponível');return;}
  const generation=++this.generation;this.frames=0;this.lastFrame=0;
  try{this.stream=this.canvas.captureStream(fps);const track=this.stream.getVideoTracks()[0];if(!track)throw Error('Sem track de vídeo');this.video.srcObject=this.stream;this.video.muted=true;this.video.playsInline=true;this.video.hidden=false;
   track.addEventListener('ended',()=>{if(generation===this.generation)this.report('stalled','Track encerrada');});
   await this.video.play();if(generation!==this.generation)return;this.report('preview');
   if(typeof this.video.requestVideoFrameCallback==='function'){const frame=()=>{if(generation!==this.generation)return;this.frames++;this.lastFrame=performance.now();this.callback=this.video.requestVideoFrameCallback(frame);};this.callback=this.video.requestVideoFrameCallback(frame);}
  }catch(e){if(generation!==this.generation)return;this.stop();this.report('unsupported',String(e.message||e));}
 }
 async enter(){if(!this.stream||this.video.readyState<2){this.report('unsupported','Prepare o vídeo real antes de abrir PiP');return;}
  try{if(document.pictureInPictureEnabled&&typeof this.video.requestPictureInPicture==='function'){await this.video.requestPictureInPicture();}else if(this.video.webkitSupportsPresentationMode?.('picture-in-picture')&&typeof this.video.webkitSetPresentationMode==='function'){this.video.webkitSetPresentationMode('picture-in-picture');}else{this.report('unsupported','PiP não disponível para este MediaStream');return;}
   this.sync();if(!this.active)this.report('requested','Aguardando confirmação do sistema');
  }catch(e){this.report('unsupported',String(e.message||e));}
 }
 async exit(){try{if(document.pictureInPictureElement===this.video)await document.exitPictureInPicture();else if(this.video.webkitPresentationMode==='picture-in-picture')this.video.webkitSetPresentationMode('inline');}catch(e){this.report('error',String(e.message||e));}this.sync();}
 stop(){this.generation++;if(this.callback!==null)this.video.cancelVideoFrameCallback?.(this.callback);this.callback=null;for(const t of this.stream?.getTracks()??[])t.stop();this.stream=null;this.video.pause();this.video.srcObject=null;this.video.hidden=true;this.active=false;this.report('idle');}
}
