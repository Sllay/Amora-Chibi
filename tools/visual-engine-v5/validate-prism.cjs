// Separate browser process avoids contention between live WebGL pages and CDP input.
const puppeteer=require('puppeteer-core'),assert=require('node:assert/strict'),fs=require('node:fs');
let stage='launch',browser;
const mark=value=>{stage=value;console.log('PRISM_CERT_STAGE '+value);};
(async()=>{
 try{
  browser=await puppeteer.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',headless:true,protocolTimeout:45000,args:['--no-sandbox','--disable-dev-shm-usage','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
  const root='http://127.0.0.1:8767/avatar/v5';
  const password=fs.readFileSync('.cert-core/preview-password.txt','utf8').trim();
  mark('authenticated lab');
  const lab=await browser.newPage();
  await lab.authenticate({username:'bkxd',password});
  await lab.setViewport({width:390,height:844});
  await lab.goto(root+'/',{waitUntil:'domcontentloaded',timeout:30000});
  await lab.waitForFunction(()=>window.AmoraVisualEngineV5?.ready,{timeout:45000});
  // Stop unnecessary continuous drawing while testing the settings UI.
  await lab.evaluate(()=>window.AmoraVisualEngineV5.stop());
  mark('generate via UI');
  await lab.bringToFront();
  await lab.evaluate(()=>document.querySelector('#prism-generate').click());
  await lab.waitForFunction(()=>!document.querySelector('#prism-generate').disabled&&document.querySelector('#prism-status').textContent.length>0,{timeout:15000});
  const result=await lab.evaluate(()=>({link:document.querySelector('#prism-url').value,status:document.querySelector('#prism-status').textContent}));
  assert.ok(result.link.startsWith(root+'/prism/'),'link generation failed: '+(result.link?'unexpected URL':result.status));
  assert.ok(!result.link.includes(password),'password leaked');
  const prefix=new URL(result.link).pathname.split('/live-overlay/')[0];
  await lab.close();
  mark('isolated overlay without Basic auth');
  const context=await browser.createBrowserContext(),page=await context.newPage();
  const errors=[];page.on('pageerror',()=>errors.push('page error'));
  await page.setViewport({width:1280,height:720});
  const response=await page.goto(result.link,{waitUntil:'domcontentloaded',timeout:30000});
  assert.equal(response.status(),200);
  await page.waitForFunction(()=>document.body.dataset.state==='ready'||document.body.dataset.state==='error',{timeout:45000});
  assert.equal(await page.evaluate(()=>document.body.dataset.state),'ready');
  mark('isolated visual WebSocket');
  await page.waitForFunction(()=>document.body.dataset.coreStatus==='Core conectado',{timeout:20000});
  const state=await page.evaluate(()=>({stored:localStorage.getItem('amora.visual.readonly.v1'),frames:window.AmoraPresentation.runtime.frames,gl:window.AmoraPresentation.runtime.gl.getError(),moc:window.AmoraPresentation.runtime.mocHash}));
  assert.equal(state.stored,null);assert.equal(state.gl,0);assert.ok(state.frames>0);assert.deepEqual(errors,[]);
  assert.equal((await fetch('http://127.0.0.1:8767'+prefix+'/app.js')).status,404);
  await context.close();
  fs.writeFileSync('evidence/prism.json',JSON.stringify({render:true,connected:true,isolated:true,glError:state.gl,moc:state.moc,physicalPRISM:false},null,2));
  console.log('PRISM_CERT_PASS');
 }finally{if(browser)await browser.close();}
})().catch(e=>{console.error('PRISM_CERT_FAILURE stage='+stage+' '+String(e.message).replace(/\/avatar\/v5\/prism\/[^/\s]+/g,'/avatar/v5/prism/[redacted]'));process.exitCode=1;});
