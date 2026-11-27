const puppeteer=require('puppeteer-core'),assert=require('node:assert/strict'),fs=require('node:fs');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{const browser=await puppeteer.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--enable-unsafe-swiftshader','--use-angle=swiftshader','--autoplay-policy=no-user-gesture-required','--disable-background-timer-throttling','--disable-renderer-backgrounding','--disable-backgrounding-occluded-windows']});
const base='http://127.0.0.1:8767',root=base+'/avatar/v5',errors=[],results={};
try{
 const password=fs.readFileSync('.cert-core/preview-password.txt','utf8').trim();
 const privatePaths=['/','/identity.json','/vendor/live2dcubismcore.min.js','/live-overlay/','/companion/'];
 for(const path of privatePaths){const response=await fetch(root+path);assert.equal(response.status,401);assert.ok(response.headers.get('www-authenticate')?.startsWith('Basic '));}
 assert.equal((await fetch(root+'/',{headers:{Authorization:'Basic '+Buffer.from('preview:wrong').toString('base64')}})).status,401);
 const privatePage=async()=>{const page=await browser.newPage();await page.authenticate({username:'preview',password});return page;};
 const p=await privatePage();p.on('pageerror',e=>errors.push(e.message));await p.setViewport({width:390,height:844});
 await p.goto(root+'/',{waitUntil:'networkidle0'});await p.waitForFunction(()=>window.AmoraVisualEngineV5?.ready,{timeout:60000});
 await p.type('#visual-pair input',fs.readFileSync('.cert-core/pairing-test.txt','utf8').trim());await p.click('#visual-pair [data-remember]');await p.click('#visual-pair button[type=submit]');
 await p.waitForFunction(()=>document.body.dataset.coreStatus==='Core conectado',{timeout:30000});
 const o=await privatePage();o.on('pageerror',e=>errors.push(e.message));await o.setViewport({width:1280,height:720});await o.goto(root+'/live-overlay/?profile=eco&scale=40&x=100&y=100',{waitUntil:'networkidle0'});await o.waitForFunction(()=>document.body.dataset.state==='ready'&&document.body.dataset.coreStatus==='Core conectado',{timeout:60000});
 await p.close();
 const c=await privatePage();c.on('pageerror',e=>errors.push(e.message));await c.goto(root+'/companion/',{waitUntil:'networkidle0'});await c.waitForFunction(()=>document.body.dataset.state==='ready',{timeout:60000});
 // Existing security limit is 2 sockets per peer: close lab before waiting for third.
 await c.waitForFunction(()=>document.body.dataset.coreStatus==='Core conectado',{timeout:45000});
 const ids=await Promise.all([o,c].map(page=>page.evaluate(()=>window.AmoraPresentation.runtime.mocHash)));assert.equal(ids[0],ids[1]);
 // Real network relay across isolated browser contexts; no BroadcastChannel shared.
 await o.close();
 const producerContext=await browser.createBrowserContext();const producer=await producerContext.newPage();
 await producer.goto(base+'/health');await producer.evaluate(async token=>{const {VisualAudioTap}=await import('/web/visual-audio-tap.mjs');const audio=new AudioContext();await audio.resume();const tap=new VisualAudioTap(audio);tap.enable(token);const oscillator=audio.createOscillator(),gain=audio.createGain();gain.gain.value=.2;oscillator.connect(gain);gain.connect(tap.node);oscillator.start();window.certAudio={audio,tap,oscillator};},fs.readFileSync('.cert-core/web-test-token.txt','utf8').trim());
 await producer.waitForFunction(()=>window.certAudio.tap.socket?.readyState===1,{timeout:12000});
 await c.bringToFront();await c.waitForFunction(()=>window.AmoraPresentation.runtime.readParameters().ParamMouthOpenY>.12,{timeout:15000});
 const open=await c.evaluate(()=>window.AmoraPresentation.runtime.readParameters().ParamMouthOpenY);
 await producer.evaluate(()=>{window.certAudio.oscillator.stop();window.certAudio.tap.disable();return window.certAudio.audio.close();});
 await c.waitForFunction(()=>window.AmoraPresentation.runtime.readParameters().ParamMouthOpenY<.02,{timeout:15000});results.audio={open,closed:await c.evaluate(()=>window.AmoraPresentation.runtime.readParameters().ParamMouthOpenY),source:'real WebAudio sine fixture over authenticated WebSocket across isolated contexts; no provider calls'};
 await producerContext.close();await c.close();
 const perf=await privatePage();perf.on('pageerror',e=>errors.push(e.message));await perf.setViewport({width:1280,height:720});
 await perf.goto(root+'/live-overlay/?profile=eco&scale=40&x=100&y=100',{waitUntil:'networkidle0'});
 await perf.bringToFront();await perf.evaluate(()=>{localStorage.removeItem('amora.visual.readonly.v1');});await perf.reload({waitUntil:'networkidle0'});await perf.waitForFunction(()=>document.body.dataset.state==='ready',{timeout:60000});
 results.profiles={};for(const profile of ['eco','balanced','high']){
  await perf.evaluate(profile=>{const r=window.AmoraPresentation.runtime;r.setProfile(profile);r.frameTimes=[];},profile);
  const before=await perf.evaluate(()=>window.AmoraPresentation.runtime.frames);const start=Date.now();await sleep(15000);
  const sample=await perf.evaluate(()=>{const r=window.AmoraPresentation.runtime;return {...r.diagnostics(),glError:r.gl.getError(),heap:performance.memory?.usedJSHeapSize??null};});
  sample.observedFps=(sample.frames-before)/((Date.now()-start)/1000);assert.ok(sample.observedFps>1,profile+' no sustained render');assert.equal(sample.glError,0);results.profiles[profile]=sample;
 }
 await perf.screenshot({path:'evidence/cert-overlay.png',omitBackground:true});await perf.close();assert.deepEqual(errors,[]);results.model=ids[0];results.device='Chromium SwiftShader CI';results.physical='BLOCKED_NOT_RUN';fs.writeFileSync('evidence/run04.json',JSON.stringify(results,null,2));console.log('RUN04 '+JSON.stringify(results));
 }finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
