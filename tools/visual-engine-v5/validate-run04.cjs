const puppeteer=require('puppeteer-core'),assert=require('node:assert/strict'),fs=require('node:fs');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{const browser=await puppeteer.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--enable-unsafe-swiftshader','--use-angle=swiftshader','--autoplay-policy=no-user-gesture-required']});
const base='http://127.0.0.1:8767',root=base+'/avatar/v5',errors=[],results={};
try{
 const p=await browser.newPage();p.on('pageerror',e=>errors.push(e.message));await p.setViewport({width:390,height:844});
 await p.goto(root+'/',{waitUntil:'networkidle0'});await p.waitForFunction(()=>window.AmoraVisualEngineV5?.ready,{timeout:60000});
 await p.type('#visual-pair input',fs.readFileSync('.cert-core/pairing-test.txt','utf8').trim());await p.click('#visual-pair [data-remember]');await p.click('#visual-pair button[type=submit]');
 await p.waitForFunction(()=>document.body.dataset.coreStatus==='Core conectado',{timeout:30000});
 const o=await browser.newPage();o.on('pageerror',e=>errors.push(e.message));await o.setViewport({width:1280,height:720});await o.goto(root+'/live-overlay/?profile=eco&scale=40&x=100&y=100',{waitUntil:'networkidle0'});await o.waitForFunction(()=>document.body.dataset.state==='ready'&&document.body.dataset.coreStatus==='Core conectado',{timeout:60000});
 const c=await browser.newPage();c.on('pageerror',e=>errors.push(e.message));await c.goto(root+'/companion/',{waitUntil:'networkidle0'});await c.waitForFunction(()=>document.body.dataset.state==='ready',{timeout:60000});
 // Existing security limit is 2 sockets per peer: close lab before waiting for third.
 await p.close();await c.waitForFunction(()=>document.body.dataset.coreStatus==='Core conectado',{timeout:45000});
 const ids=await Promise.all([o,c].map(page=>page.evaluate(()=>window.AmoraPresentation.runtime.mocHash)));assert.equal(ids[0],ids[1]);
 // Test real analyser -> local envelope -> actual rendered model; sine fixture is not provider TTS.
 await c.bringToFront();await c.evaluate(async()=>{const {VisualAudioTap}=await import('/web/visual-audio-tap.mjs');const audio=new AudioContext();await audio.resume();const tap=new VisualAudioTap(audio);tap.enable();const oscillator=audio.createOscillator(),gain=audio.createGain();gain.gain.value=.2;oscillator.connect(gain);gain.connect(tap.node);oscillator.start();window.certAudio={audio,tap,oscillator};});
 await c.waitForFunction(()=>window.AmoraPresentation.runtime.readParameters().ParamMouthOpenY>.12,{timeout:12000});
 const open=await c.evaluate(()=>window.AmoraPresentation.runtime.readParameters().ParamMouthOpenY);await c.evaluate(()=>{window.certAudio.oscillator.stop();window.certAudio.tap.disable();return window.certAudio.audio.close();});
 await c.waitForFunction(()=>window.AmoraPresentation.runtime.readParameters().ParamMouthOpenY<.02,{timeout:12000});results.audio={open,closed:await c.evaluate(()=>window.AmoraPresentation.runtime.readParameters().ParamMouthOpenY),source:'real WebAudio sine fixture, no provider calls'};
 await c.close();
 await o.bringToFront();await o.evaluate(()=>{localStorage.removeItem('amora.visual.readonly.v1');});await o.reload({waitUntil:'networkidle0'});await o.waitForFunction(()=>document.body.dataset.state==='ready',{timeout:60000});
 results.profiles={};for(const profile of ['eco','balanced','high']){
  await o.evaluate(profile=>{const r=window.AmoraPresentation.runtime;r.setProfile(profile);r.frameTimes=[];},profile);
  const before=await o.evaluate(()=>window.AmoraPresentation.runtime.frames);const start=Date.now();await sleep(15000);
  const sample=await o.evaluate(()=>{const r=window.AmoraPresentation.runtime;return {...r.diagnostics(),glError:r.gl.getError(),heap:performance.memory?.usedJSHeapSize??null};});
  sample.observedFps=(sample.frames-before)/((Date.now()-start)/1000);assert.ok(sample.observedFps>1,profile+' no sustained render');assert.equal(sample.glError,0);results.profiles[profile]=sample;
 }
 await o.screenshot({path:'evidence/cert-overlay.png',omitBackground:true});await o.close();assert.deepEqual(errors,[]);results.model=ids[0];results.device='Chromium SwiftShader CI';results.physical='BLOCKED_NOT_RUN';fs.writeFileSync('evidence/run04.json',JSON.stringify(results,null,2));console.log('RUN04 '+JSON.stringify(results));
 }finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
