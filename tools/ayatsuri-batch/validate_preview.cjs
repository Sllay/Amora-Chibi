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
  await page.click('[data-expr="WinkLeft"]');
  await new Promise(resolve=>setTimeout(resolve,500));
  const wink=await digest();
  await page.screenshot({path:path.join(path.dirname(html),"amora-browser-wink-left.png"),fullPage:true});
  if(errors.length)throw Error("Browser errors: "+errors.join(" | "));
  if(neutral.corner!==0||sleepy.corner!==0||surprised.corner!==0||wink.corner!==0)throw Error("Opaque background remains around Amora");
  if(!neutral.center||!sleepy.center||!surprised.center||!wink.center)throw Error("Center of Amora is not rendered");
  if(neutral.hash===sleepy.hash||sleepy.hash===surprised.hash||wink.hash===surprised.hash)throw Error("Expressions did not change canvas pixels");
  console.log(JSON.stringify({ok:true,loaded_layers:12,neutral,sleepy,surprised,wink,errors},null,2));
 }finally{await browser.close();}
}
run().catch(err=>{console.error(err.stack||err);process.exitCode=1;});
