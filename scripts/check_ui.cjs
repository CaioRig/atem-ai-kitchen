const { chromium } = require('../data/ui-check/node_modules/playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const root = path.resolve('client/public');
const server = http.createServer((req,res) => {
  const file = path.join(root, req.url === '/' ? 'index.html' : req.url);
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  fs.readFile(file, (e,b) => { res.statusCode = e ? 404 : 200; res.end(e ? '' : b); });
});
(async () => {
  await new Promise(r => server.listen(0,'127.0.0.1',r));
  const browser = await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const errors=[];
  try {
    const context = await browser.newContext({hasTouch:true});
    const page = await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(()=> {localStorage.setItem('atem.mic','off');localStorage.setItem('jarvis.token','ui-test');});
    let socket;
    await page.routeWebSocket('**/ws', ws=> {
      socket=ws;
      ws.onMessage(raw=> {
        const m=JSON.parse(raw);
        if(m.type==='hello') ws.send(JSON.stringify({type:'welcome',state:'idle',detail:'',wake_word:'hey Luna',debug:false,memory:{available:true,count:12},wake:{available:true},llm:{available:true},tts:{available:true},transcription:{available:true}}));
        if(m.type==='memory_list') ws.send(JSON.stringify({type:'memory',action:'list',count:12,items:Array.from({length:12},(_,i)=>({id:i+1,kind:'preference',text:'Prefiro receitas leves com legumes e ingredientes disponíveis na cozinha. '.repeat(3)}))}));
      });
    });
    const url=`http://127.0.0.1:${server.address().port}/`;
    await page.goto(url);
    await page.waitForFunction(()=>document.querySelector('#conn').textContent.includes('conectado'));
    const screen=async action=> {socket.send(JSON.stringify({type:'screen',action}));await page.waitForTimeout(60);};
    const audit=async()=> {
      const result=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,
        small:[...document.querySelectorAll('button,select')].filter(e=>e.getClientRects().length && !e.closest('.hidden')).filter(e=>e.getBoundingClientRect().width<48 || e.getBoundingClientRect().height<48).map(e=>e.id)}));
      assert.equal(result.overflow,false); assert.deepEqual(result.small,[]);
    };
    for(const [w,h] of [[360,800],[768,1024],[1024,768],[1366,768],[1920,1080],[800,360]]) {
      await page.setViewportSize({width:w,height:h});
      await screen('home');
      socket.send(JSON.stringify({type:'reply',final:true,text:'Receita de legumes\n'+('1. Separe os ingredientes, lave e prepare com cuidado.\n').repeat(35)}));
      await page.waitForTimeout(100);
      await audit();
      await screen('down');
      assert(await page.locator('#content-scroll').evaluate(e=>e.scrollTop>0));
      await page.screenshot({path:`data/ui-check/main-${w}x${h}.png`});
      await page.locator('#settings-open').tap(); await audit();
      await page.screenshot({path:`data/ui-check/settings-${w}x${h}.png`});
      await screen('memory'); await page.waitForTimeout(100); await audit();
      assert(await page.locator('#settings-panel').evaluate(e=>e.classList.contains('hidden')));
      await screen('down');assert(await page.locator('#memory-list').evaluate(e=>e.scrollTop>0));
      await page.screenshot({path:`data/ui-check/memory-${w}x${h}.png`});
      socket.send(JSON.stringify({type:'weather',available:true,summary:'Hoje: sol',detail:'',forecast:{text:'Amanhã, terça-feira: parcialmente nublado, mínima 15 e máxima 25 graus, chuva 30%. '.repeat(12)}}));
      await page.waitForTimeout(100);await audit();
      assert(await page.locator('#memory-panel').evaluate(e=>e.classList.contains('hidden')));
      await screen('close');assert(await page.locator('#forecast-panel').evaluate(e=>e.classList.contains('hidden')));
      console.log(`${w}x${h}: toque, rolagem, textos longos, paineis e limites OK`);
    }
    await page.setViewportSize({width:360,height:800});
    await screen('settings');
    for(let i=0;i<8;i++) await screen('larger');
    assert.equal(await page.locator('#text-size').textContent(),'150%');await audit();
    await page.reload();await page.waitForFunction(()=>document.querySelector('#conn').textContent.includes('conectado'));
    assert.equal(await page.locator('#text-size').textContent(),'150%');
    await screen('settings');await page.locator('#visual-reset').tap();
    assert.equal(await page.locator('#text-size').textContent(),'100%');
    // Equivalente de viewport CSS de 1366x768 em zoom 200%, com texto ampliado.
    await page.setViewportSize({width:683,height:384});
    await screen('larger');await screen('larger');await audit();
    await page.screenshot({path:'data/ui-check/zoom-200-equivalent.png'});
    assert.deepEqual(errors,[]);
    console.log('Preferencias persistidas/restauradas, limite 150%, viewport equivalente a zoom 200%, sem erros JS.');
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});

