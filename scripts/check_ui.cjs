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
    await page.addInitScript(()=> {
      localStorage.setItem('atem.mic','off');localStorage.setItem('jarvis.token','ui-test');
      Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>4});
      window.facePaints=0;
      const fill=CanvasRenderingContext2D.prototype.fillRect;
      CanvasRenderingContext2D.prototype.fillRect=function(x,y,w,h) {
        if(this.canvas.id==='face' && w===48 && h===27) window.facePaints++;
        return fill.call(this,x,y,w,h);
      };
    });
    let socket;
    const deleted=[];
    await page.routeWebSocket('**/ws', ws=> {
      socket=ws;
      ws.onMessage(raw=> {
        const m=JSON.parse(raw);
        if(m.type==='hello') ws.send(JSON.stringify({type:'welcome',state:'idle',detail:'',wake_word:'hey Luna',debug:false,memory:{available:true,count:12},wake:{available:true},llm:{available:true},tts:{available:true},transcription:{available:true}}));
        if(m.type==='memory_list') ws.send(JSON.stringify({type:'memory',action:'list',count:13,items:[
          {id:42,kind:'ingredient',text:'ovos: 3 unidades'},
          ...Array.from({length:12},(_,i)=>({id:i+1,kind:'preference',text:'Prefiro receitas leves com legumes e ingredientes disponíveis na cozinha. '.repeat(3)}))
        ]}));
        if(m.type==='memory_delete') deleted.push(m.id);
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
      await audit();
      if(w===360 || w===1366) {
        socket.send(JSON.stringify({type:'reply',text:'Com ovos e legumes, você pode fazer uma omelete de frigideira.',final:true}));
        socket.send(JSON.stringify({type:'weather',available:true,location:'Ouro Fino, MG',summary:'23 °C · sol',
          updated_at:Date.now()/1000,status:'cache'}));
        socket.send(JSON.stringify({type:'transcript',text:'O que posso fazer para o jantar?',final:true}));
        await page.locator('.weather-temperature').waitFor();
        await page.screenshot({path:`data/ui-check/luna-home-${w}x${h}.png`,style:'#toast { visibility: hidden }'});
      }
      socket.send(JSON.stringify({type:'state',state:'thinking',detail:''}));
      socket.send(JSON.stringify({type:'reply',final:true,text:'Receita de legumes\n'+('1. Separe os ingredientes, lave e prepare com cuidado.\n').repeat(35)}));
      socket.send(JSON.stringify({type:'state',state:'idle',detail:''}));
      await page.waitForTimeout(100);
      await audit();
      await screen('down');
      assert(await page.locator('#stage-scroll').evaluate(e=>e.scrollTop>0));
      await page.screenshot({path:`data/ui-check/main-${w}x${h}.png`});
      await page.locator('#settings-open').tap(); await audit();
      await page.screenshot({path:`data/ui-check/settings-${w}x${h}.png`});
      await screen('memory'); await page.waitForTimeout(100); await audit();
      assert(await page.locator('#settings-panel').evaluate(e=>e.classList.contains('hidden')));
      await screen('down');assert(await page.locator('#memory-list').evaluate(e=>e.scrollTop>0));
      await page.screenshot({path:`data/ui-check/memory-${w}x${h}.png`});
      socket.send(JSON.stringify({type:'weather',available:true,summary:'Hoje: sol',detail:'',forecast:{text:'Amanhã, terça-feira: parcialmente nublado, mínima 15 e máxima 25 graus, chuva 30%. '.repeat(12)}}));
      await page.waitForTimeout(100);await audit();
      await screen('close');
      await screen('close');assert(await page.locator('#stage-content').evaluate(e=>e.classList.contains('hidden')));
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

    await page.setViewportSize({width:1366,height:768});
    await page.locator('#visual-reset').click();
    await page.locator('#theme').selectOption('dark');
    assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
    await page.keyboard.press('Escape');
    await page.screenshot({path:'data/ui-check/luna-dark.png',style:'#toast { visibility: hidden }'});
    await page.reload();
    await page.waitForFunction(()=>document.querySelector('#conn').textContent.includes('conectado'));
    assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');

    await page.locator('#settings-open').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'settings-close');
    assert(await page.locator('#app').evaluate(e=>e.inert));
    await page.locator('#fullscreen').click();
    await page.waitForFunction(()=>document.fullscreenElement===document.documentElement);
    await page.locator('#fullscreen').click();
    await page.waitForFunction(()=>!document.fullscreenElement);
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'settings-open');

    await page.locator('#ingredients-open').click();
    await page.locator('.ingredient-amount').waitFor();
    assert.equal(await page.locator('#memory-list li').count(),1);
    assert.equal(await page.locator('.ingredient-amount').textContent(),'3 unidades');
    assert(await page.locator('#memory-clear').isHidden());
    page.once('dialog', dialog=>dialog.dismiss());
    await page.getByRole('button',{name:'Remover: ovos'}).click();
    assert.deepEqual(deleted,[]);
    page.once('dialog', dialog=>dialog.accept());
    await page.getByRole('button',{name:'Remover: ovos'}).click();
    await page.waitForTimeout(100);
    assert.deepEqual(deleted,[42]);
    await page.keyboard.press('Escape');
    await page.locator('#memory').click();
    await page.locator('#memory-clear').click();
    assert.match(await page.locator('#memory-clear').textContent(),/Confirmar/);
    await page.keyboard.press('Escape');

    const cdp=await context.newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
    try {
      assert(await page.locator('html').evaluate(e=>e.classList.contains('economic-client')));
      for(const [state,label,detail=''] of [['wake','Escutando'],['listening','Escutando'],['transcribing','Transcrevendo'],
        ['thinking','Pensando'],['thinking','Executando comando','Procurando vídeos…'],['speaking','Falando'],['idle','Aguardando'],['error','Erro']]) {
        socket.send(JSON.stringify({type:'state',state,detail}));
        await page.waitForFunction(label=>document.querySelector('#mood').textContent===label,label);
      }
      const frames=()=>page.evaluate(()=>window.facePaints);
      const first=await frames();
      await page.waitForFunction(first=>window.facePaints>first,first);
      await page.locator('#recipes-open').click();
      assert.match(await page.locator('#stage-scroll').textContent(),/panquecas/i);
      await page.waitForTimeout(250);
      const hidden=await frames();
      await page.waitForTimeout(350);
      assert.equal(await frames(),hidden);
      await page.keyboard.press('Escape');
      assert.equal(await page.evaluate(()=>document.activeElement.id),'recipes-open');
      await page.emulateMedia({reducedMotion:'reduce'});
      await page.waitForTimeout(250);
      const still=await frames();
      await page.waitForTimeout(350);
      assert.equal(await frames(),still);
    } finally {
      await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});
    }
    assert.deepEqual(errors,[]);
    console.log('OK: teclado e foco, tela cheia, tema, despensa e confirmação, estados e Canvas econômico com CPU limitada e movimento reduzido.');
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});

