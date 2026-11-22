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
      const eye=box("eye_white_"+side), lid=box(kind+"_lid_"+side);
      const upper=kind==="upper", edge=eye.cy+eye.h*(upper?-.31:.31);
      const movement=lidY(eye.cx,edge,eye,lid,.35,upper);
      const cover=skinY(eye.cx,edge,eye,lid,.35,upper);
      const anchor=eye.cy+eye.h*(upper?-.95:.95);
      return {side,kind,delta:Math.abs(movement-cover),
       anchorDelta:Math.abs(skinY(eye.cx,anchor,eye,lid,.35,upper)-anchor)};
    })),
   };
  });
  if(lidChecks.leftLashReach<.2||lidChecks.rightLashReach<.2){
   throw Error("Closed-eye lash hooks missing or pointed inward: "+JSON.stringify(lidChecks));
  }
  for(const a of lidChecks.attachments){
    if(a.delta>0.001||a.anchorDelta>0.001)throw Error("Face skin detached from eyelash movement: "+JSON.stringify(a));
  }
  for(const p of lidChecks.samples){
   if(p.opening>=.06 && (p.eyeAlpha<.999||p.closed>.001)) {
    throw Error("Iris faded before lashes met: "+JSON.stringify(p));
   }
  }
  for(let i=1;i<lidChecks.samples.length;i++){
   if(lidChecks.samples[i].progress>lidChecks.samples[i-1].progress||
      lidChecks.samples[i].closed>lidChecks.samples[i-1].closed){
    throw Error("Eyelid closing shape progression is not monotonic");
   }
  }

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
  console.log(JSON.stringify({ok:true,loaded_layers:18,lidChecks,neutral,sleepy,surprised,wink,half,near,closed,errors},null,2));
 }finally{await browser.close();}
}
run().catch(err=>{console.error(err.stack||err);process.exitCode=1;});
