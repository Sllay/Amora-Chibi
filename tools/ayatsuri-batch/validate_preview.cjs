#!/usr/bin/env node
"use strict";
// Cross-browser smoke test with real rendering: never accept static HTML as a
// substitute for a working animation. Runs on the GitHub Actions Ubuntu Chrome.
const fs=require("node:fs");
const path=require("node:path");
const crypto=require("node:crypto");
const puppeteer=require("puppeteer-core");

async function run() {
 const html=path.resolve(process.argv[2]||"artifacts/amora-mobile-preview.html");
 if(!fs.existsSync(html)) throw Error("Missing preview "+html);
 const chrome=["/usr/bin/google-chrome","/usr/bin/google-chrome-stable","/usr/bin/chromium"].find(fs.existsSync);
 if(!chrome) throw Error("No installed Chrome found for browser smoke test");
 const browser=await puppeteer.launch({headless:true,executablePath:chrome,args:["--no-sandbox","--disable-dev-shm-usage"]});
 try{
  const page=await browser.newPage({viewport:{width:430,height:932},deviceScaleFactor:1});
  const errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  await page.goto("file://"+html,{waitUntil:"load",timeout:45000});
  await page.waitForFunction(()=>document.querySelector("#status")?.textContent.includes("prévia pronta"),{timeout:45000});
  await page.waitForFunction(()=>!!document.querySelector("canvas")?.getContext("2d")?.getImageData(600,600,1,1)?.data[3],{timeout:30000});
  // Geometry tests in the real browser, not just "canvas hash changed".
  const lidChecks=await page.evaluate(()=>{
   const samples=[0,.03,.06,.18,.35,.5,.65,1].map(opening=>({
    opening, progress:lidProgress(opening), closed:closedPoseAlpha(opening), eyeAlpha:eyeVisibility(opening)
   }));
   const leftEye=box("eye_white_L"),rightEye=box("eye_white_R");
   const leftPose=box("closed_smile_L"),rightPose=box("closed_smile_R");
   return {samples,
    leftLashReach:(leftEye.x-leftPose.x)/leftEye.w,
    rightLashReach:(rightPose.x+rightPose.w-rightEye.x-rightEye.w)/rightEye.w,
    attachments:["L","R"].flatMap(side=>["upper","lower"].map(kind=>{
      const eye=box("eye_white_"+side);
      const upper=box("upper_lid_"+side),lower=box("lower_lid_"+side);
      const onTop=kind==="upper", y=eye.cy+eye.h*(onTop?-.5:.5);
      const ap=apertureY(eye.cx,y,eye,upper,lower,.35);
      const lash=lidY(eye.cx,y,eye,onTop?upper:lower,.35,onTop);
      const opened=apertureY(eye.cx,y,eye,upper,lower,1);
      return {side,kind,delta:Math.abs(ap-lash),anchorDelta:Math.abs(opened-y)};
    })),
   };
  });
  if(lidChecks.leftLashReach<.2||lidChecks.rightLashReach<.2){
   throw Error("Closed-eye lash hooks missing or pointed inward: "+JSON.stringify(lidChecks));
  }
  for(const a of lidChecks.attachments){
    if(a.delta>0.001||a.anchorDelta>0.001)throw Error("Sclera aperture detached from eyelash boundary: "+JSON.stringify(a));
  }
  for(const p of lidChecks.samples){
   if(p.opening>=.115 && (p.eyeAlpha<.999||p.closed>.001)) {
    throw Error("Closed pose appeared while eye was still open: "+JSON.stringify(p));
   }
   if(Math.abs(p.eyeAlpha+p.closed-1)>0.002){
    throw Error("Closed pose and open-eye opacities are not complementary: "+JSON.stringify(p));
   }
  }
  for(let i=1;i<lidChecks.samples.length;i++){
   if(lidChecks.samples[i].progress>lidChecks.samples[i-1].progress||
      lidChecks.samples[i].closed>lidChecks.samples[i-1].closed){
    throw Error("Eyelid closing shape progression is not monotonic");
   }
  }

  // Independently inspect rasterized RGBA, not a comparison of the same
  // geometry function against itself. Nasal limits come from original lash
  // PNG alpha and each sample renders the actual eye/iris code.
  const eyeCoverage=await page.evaluate(()=>{
    const openings=[1,.98,.95,.85,.7,.5,.35,.18,.115,.06,0];
    const gazes=[[0,0],[-1.25,0],[1.25,0],[0,-1.25],[0,1.25],[-1.25,-1.25],[1.25,1.25]];
    const report={samples:0,eyePixels:0,nasalLeakPixels:0,verticalLeakPixels:0,
      unpaintedSkinPixels:0,closedEyePixels:0,worst:[],sides:[]};
    function sourceAlphaBounds(id){
      const b=box(id),c=document.createElement("canvas");c.width=b.w;c.height=b.h;
      const g=c.getContext("2d",{willReadFrequently:true});
      g.drawImage(images[id],b.x,b.y,b.w,b.h,0,0,b.w,b.h);
      const data=g.getImageData(0,0,b.w,b.h).data;
      let min=b.w,max=-1;
      for(let y=0;y<b.h;y++)for(let x=0;x<b.w;x++)if(data[(y*b.w+x)*4+3]>=96){
        min=Math.min(min,x);max=Math.max(max,x);
      }
      if(max<min)throw Error("No visible lash pixels: "+id);
      return [b.x+min,b.x+max+1];
    }
    ctx.save();ctx.setTransform(1,0,0,1,0,0);ctx.globalAlpha=1;
    try{
      for(const side of ["L","R"]){
        const b=box("eye_white_"+side),orig=source.layers["eye_white_"+side].sourceBox;
        if(!orig)throw Error("Original eye bounds absent");
        const u=sourceAlphaBounds("upper_lid_"+side),l=sourceAlphaBounds("lower_lid_"+side);
        const left=(orig[0]+orig[2])/2<W/2;
        const cap=left?Math.min(u[1],l[1]):Math.max(u[0],l[0]);
        const top=box("upper_lid_"+side),low=box("lower_lid_"+side);
        const x0=Math.floor(Math.min(orig[0],top.x,low.x))-3;
        const y0=Math.floor(Math.min(orig[1],top.y,low.y))-3;
        const x1=Math.ceil(Math.max(orig[2],top.x+top.w,low.x+low.w))+3;
        const y1=Math.ceil(Math.max(orig[3],top.y+top.h,low.y+low.h))+3;
        const w=x1-x0,h=y1-y0;
        const sample=()=>ctx.getImageData(x0,y0,w,h).data;
        const clear=()=>ctx.clearRect(0,0,W,H);
        const isNasal=x=>left?x>=cap:x<cap;
        clear();draw("body_horns");
        const body=sample();
        let skinArea=0;
        // Actual former eye rectangle at the nose must still be solid skin.
        for(let y=orig[1];y<orig[3];y++)for(let x=orig[0];x<orig[2];x++){
          if(!isNasal(x))continue;
          skinArea++;
          if(body[((y-y0)*w+x-x0)*4+3]<250)report.unpaintedSkinPixels++;
        }
        let openArea=0;
        for(const opening of openings){
          const upper=new Int32Array(w).fill(h),lower=new Int32Array(w).fill(-1);
          if(opening>=.115){
            clear();lid(side,"upper",opening);
            const up=sample();
            clear();lid(side,"lower",opening);
            const lo=sample();
            for(let y=0;y<h;y++)for(let x=0;x<w;x++){
              if(up[(y*w+x)*4+3]>=32)upper[x]=Math.min(upper[x],y);
              if(lo[(y*w+x)*4+3]>=32)lower[x]=Math.max(lower[x],y);
            }
          }
          for(const [gx,gy] of gazes){
            clear();eye(side,opening,gx,gy,1.09);
            const data=sample();let area=0,nasal=0,vertical=0;
            for(let y=0;y<h;y++)for(let x=0;x<w;x++){
              const alpha=data[(y*w+x)*4+3];
              if(alpha===0)continue;
              area++;
              if(isNasal(x+x0))nasal++;
              // The upper/lower real lash raster is the independent bound.
              // Two/three pixels of strip AA are tolerated, not a visible
              // sliver. Check every column that has both physical lashes.
              if(alpha>=32&&upper[x]<h&&lower[x]>=0&&upper[x]<=lower[x]&&
                  (y<upper[x]-3||y>lower[x]+3))vertical++;
            }
            report.samples++;report.eyePixels+=area;
            report.nasalLeakPixels+=nasal;report.verticalLeakPixels+=vertical;
            if(opening===0)report.closedEyePixels+=area;
            if(opening===1&&gx===0&&gy===0)openArea=area;
            if((nasal||vertical)&&report.worst.length<12)
              report.worst.push({side,opening,gx,gy,nasal,vertical});
          }
        }
        if(openArea<.25*b.w*b.h)throw Error("Eye disappears instead of being correctly masked: "+side);
        report.sides.push({side,cap,openArea,skinArea,derived:[b.x,b.y,b.x+b.w,b.y+b.h],original:orig});
      }
      return report;
    } finally {ctx.clearRect(0,0,W,H);ctx.restore();}
  });
  fs.writeFileSync(path.join(path.dirname(html),"amora-eye-coverage.json"),JSON.stringify(eyeCoverage,null,2));
  console.log("EYE_COVERAGE "+JSON.stringify(eyeCoverage));
  if(eyeCoverage.nasalLeakPixels||eyeCoverage.verticalLeakPixels||
     eyeCoverage.unpaintedSkinPixels||eyeCoverage.closedEyePixels)
    throw Error("Eye coverage regression: "+JSON.stringify(eyeCoverage));
  await page.waitForFunction(()=>!!document.querySelector("canvas").getContext("2d").getImageData(600,600,1,1).data[3]);
  await page.screenshot({path:path.join(path.dirname(html),"amora-browser-neutral.png"),fullPage:true});
  async function digest(){
   return page.evaluate(()=>{
    const c=document.querySelector("canvas");
    const ctx=c.getContext("2d");
    const b=ctx.getImageData(0,0,c.width,c.height).data;
    let hash=2166136261;
    for(let i=0;i<b.length;i+=29){hash^=b[i];hash=Math.imul(hash,16777619);}
    return {hash:hash>>>0,corner:ctx.getImageData(0,0,1,1).data[3],center:ctx.getImageData(627,627,1,1).data[3]};
   });
  }
  const neutral=await digest();
  // A responsive face is not just an image spinning in the Z axis. Verify
  // independent left/right and up/down head+body turn controls in Chrome.
  const setSlider=async(id,value)=>{
   await page.evaluate(({id,value})=>{
    const e=document.getElementById(id);
    e.value=String(value);
    e.dispatchEvent(new Event("input",{bubbles:true}));
   },{id,value});
   await new Promise(resolve=>setTimeout(resolve,460));
  };
  const poses={};
  for(const [id,value,name] of [
    ["headX",-26,"head-left"],["headX",26,"head-right"],
    ["headY",-25,"head-up"],["headY",25,"head-down"],
  ]){
   await setSlider("headX",id==="headX"?value:0);
   await setSlider("headY",id==="headY"?value:0);
   poses[name]=await digest();
   await page.screenshot({path:path.join(path.dirname(html),"amora-browser-"+name+".png"),fullPage:true});
  }
  await setSlider("headY",0);
  await setSlider("headX",0);
  for(const [value,name] of [[-1,"gaze-left"],[1,"gaze-right"]]){
   await setSlider("lookX",value);
   poses[name]=await digest();
   await page.screenshot({path:path.join(path.dirname(html),"amora-browser-"+name+".png"),fullPage:true});
  }
  await setSlider("lookX",0);
  if(poses["head-left"].hash===poses["head-right"].hash||
     poses["head-up"].hash===poses["head-down"].hash||
     poses["gaze-left"].hash===poses["gaze-right"].hash){
   throw Error("3/4 head/body turn or eye gaze does not change canvas rendering");
  }
  const irisChecks=await page.evaluate(()=>{
    const state=irisSpring.L;
    state.armed=false;state.age=99;
    const closed=irisSpringScale("L",0,0);
    const samples=[];
    for(let i=0;i<90;i++)samples.push(irisSpringScale("L",1,.01));
    state.armed=false;state.age=99;state.scale=1;
    const peaks=[
      Math.max(...samples.slice(0,15)),
      Math.max(...samples.slice(28,46)),
      Math.max(...samples.slice(57,76))
    ];
    const b=box("eye_white_L"),ib=box("iris_L");
    const travelX=Math.min(b.w*.22,Math.max(b.w*.12,b.w-ib.w)*.56);
    return {closed,peaks,atEnd:samples[samples.length-1],travelRatio:travelX/b.w};
  });
  if(irisChecks.closed<.94||irisChecks.closed>.96||
      irisChecks.peaks[0]<1.05||!(irisChecks.peaks[0]>irisChecks.peaks[1]&&irisChecks.peaks[1]>irisChecks.peaks[2])||
      irisChecks.peaks[2]<1.005||irisChecks.travelRatio<.09){
   throw Error("Iris shrink, damped multi-pulse rebound or gaze travel invalid: "+JSON.stringify(irisChecks));
  }
  const mouthCheck=await page.evaluate(()=>{
    const samples=[0,0.12,0.22,0.52,0.8,1].map(open=>({open,
      auto:mouthLayerWeights(open,0),
      speaking:mouthLayerWeights(open,1)
    }));
    const sad=mouthLayerWeights(.7,3);
    const flat=mouthLayerWeights(1,4),bite=mouthLayerWeights(1,5);
    if(flat[4]!==1||bite[5]!==1||presets.Sobbing.mouthPose===presets.LaughingTears.mouthPose)throw Error("New mouth semantics invalid");
    for(const side of ["L","R"]){
      const eye=box("eye_white_"+side),lid=box("lower_lid_"+side);
      if(!lowerProfiles[side].some(v=>v!==null))throw Error("Empty measured lash profile");
      for(const opening of [1,.95,.8,.65,.5,.18])for(let x=eye.x;x<eye.x+eye.w;x+=4){
        const edge=lowerEdgeAt(side,x,eye,lid);
        if(Math.abs(lowerApertureY(side,x,eye,lid,opening)-lidY(x,edge,eye,lid,opening,false))>1e-6)throw Error("Lower lash contour detached");
      }
    }
    const enabled=window.AmoraVisualEngine.setParameters({
      ParamMouthOpenY:0.22,ParamMouthPose:0,ParamBlush:.4
    });
    const after=window.AmoraVisualEngine.getParameters();
    window.AmoraVisualEngine.setParameters({ParamMouthOpenY:0,ParamBlush:0});
    return {samples,sad,enabled,after};
  });
  const quiet=mouthCheck.samples.find(x=>x.open===.22);
  const loud=mouthCheck.samples.find(x=>x.open===1);
  if(!(quiet.auto[1]>quiet.auto[0]&&quiet.auto[1]>.7))
    throw Error("Partially opened mouth must display talking sprite: "+JSON.stringify(quiet));
  if(!(loud.auto[2]>.99&&loud.speaking[2]>.99))
    throw Error("Wide open mouth must display shout even from talking pose: "+JSON.stringify(loud));
  if(!mouthCheck.enabled||Math.abs(mouthCheck.after.mouth-.22)>.01||
     Math.abs(mouthCheck.after.blush-.4)>.01||mouthCheck.sad[3]<.99)
    throw Error("Public parameter interface or sad expression incorrect: "+JSON.stringify(mouthCheck));
  for(const sample of mouthCheck.samples){
    for(const weights of [sample.auto,sample.speaking]){
      if(Math.abs(weights.reduce((a,b)=>a+b,0)-1)>.0001)
        throw Error("Mouth image opacity not normalized at "+sample.open);
    }
  }
  // Screenshots prove the selected PNGs are actually visible at small/loud
  // opening, not only that numeric opacity math appears correct.
  await setSlider("mouthPose",0);
  await setSlider("mouth",.22);
  const mouthTalk=await digest();
  await page.screenshot({path:path.join(path.dirname(html),"amora-browser-mouth-talk.png"),fullPage:true});
  await setSlider("mouth",.98);
  const mouthShout=await digest();
  await page.screenshot({path:path.join(path.dirname(html),"amora-browser-mouth-wide.png"),fullPage:true});
  await setSlider("mouth",0);
  if(mouthTalk.hash===mouthShout.hash)throw Error("Talk and wide mouth screenshots identical");
  // Regression snapshots for the newly supplied PNG art and emotion weights.
  const emotional={};
  for(const name of ["Shy","Crying","Sobbing","Sad","Angry","Shouting","LaughingTears","Deadpan","Enchanted"]){
   await page.click('[data-expr="'+name+'"]');
   await new Promise(resolve=>setTimeout(resolve,680));
   emotional[name]=await digest();
   await page.screenshot({path:path.join(path.dirname(html),"amora-browser-"+name.toLowerCase()+".png"),fullPage:true});
   if(emotional[name].hash===neutral.hash)throw Error("Emotion "+name+" did not affect canvas");
  }
  const eightExtras=await page.evaluate(()=>[
     "extra_blush","extra_tear_L","extra_tear_R",
     "extra_mouth_talk","extra_mouth_open_wide","extra_mouth_sad","extra_mouth_flat","extra_mouth_bite"
   ].every(name=>images[name]&&images[name].complete&&box(name).w>0));
  if(!eightExtras)throw Error("User-supplied expression PNG not loaded");
  await page.click('[data-expr="Sleepy"]');
  await new Promise(resolve=>setTimeout(resolve,500));
  const sleepy=await digest();
  await page.screenshot({path:path.join(path.dirname(html),"amora-browser-sleepy.png"),fullPage:true});
  await page.click('[data-expr="Surprised"]');
  await new Promise(resolve=>setTimeout(resolve,500));
  const surprised=await digest();
  await page.screenshot({path:path.join(path.dirname(html),"amora-browser-surprised.png"),fullPage:true});
  // Turn off random blink to make the transition capture deterministic.
  await page.click("#autoBlink");
  await page.click('[data-expr="WinkLeft"]');
  await new Promise(resolve=>setTimeout(resolve,700));
  const wink=await digest();
  await page.screenshot({path:path.join(path.dirname(html),"amora-browser-wink-left.png"),fullPage:true});
  await page.evaluate(()=>{
   const el=document.querySelector("#eyeL");
   el.value="0.35";
   el.dispatchEvent(new Event("input",{bubbles:true}));
  });
  await new Promise(resolve=>setTimeout(resolve,700));
  const half=await digest();
  await page.screenshot({path:path.join(path.dirname(html),"amora-browser-half-blink.png"),fullPage:true});
  // Snapshot the exact reference target: two happy CLOSED eyes that meet in
  // one shared curved line. Also exercise the transition at 18% openness.
  const setBoth=async (opening)=>{
   await page.evaluate(value=>{
    for(const id of ["eyeL","eyeR"]){
     const el=document.querySelector("#"+id);
     el.value=String(value);
     el.dispatchEvent(new Event("input",{bubbles:true}));
    }
   },opening);
   await new Promise(resolve=>setTimeout(resolve,650));
  };
  await setBoth(0.18);
  const near=await digest();
  await page.screenshot({path:path.join(path.dirname(html),"amora-browser-nearly-closed.png"),fullPage:true});
  await setBoth(0);
  const closed=await digest();
  await page.screenshot({path:path.join(path.dirname(html),"amora-browser-closed-smile.png"),fullPage:true});
  if(errors.length)throw Error("Browser errors: "+errors.join(" | "));
  if(neutral.corner!==0||sleepy.corner!==0||surprised.corner!==0||wink.corner!==0||half.corner!==0||near.corner!==0||closed.corner!==0)throw Error("Opaque background remains around Amora");
  if(!neutral.center||!sleepy.center||!surprised.center||!wink.center||!half.center||!near.center||!closed.center)throw Error("Center of Amora is not rendered");
  if(neutral.hash===sleepy.hash||sleepy.hash===surprised.hash||wink.hash===surprised.hash||half.hash===wink.hash||half.hash===neutral.hash||near.hash===half.hash||closed.hash===near.hash)throw Error("Expressions did not change canvas pixels");
  console.log(JSON.stringify({ok:true,loaded_layers:22,eightExtras,eyeCoverage,lidChecks,irisChecks,mouthCheck,mouthTalk,mouthShout,poses,emotional,neutral,sleepy,surprised,wink,half,near,closed,errors},null,2));
 }finally{await browser.close();}
}
run().catch(err=>{console.error(err.stack||err);process.exitCode=1;});
