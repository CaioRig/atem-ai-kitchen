// Chrome real, backend e player simulados; nenhuma chamada ao YouTube.
const { chromium } = require('../data/ui-check/node_modules/playwright');
const http = require('http'), fs = require('fs'), path = require('path'), assert = require('assert/strict');
const root = path.resolve('client/public');
const server = http.createServer((req,res) => {
  const file = path.join(root, req.url === '/' ? 'index.html' : req.url);
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  fs.readFile(file,(e,b)=>{res.statusCode=e?404:200;res.end(e?'':b);});
});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1366,height:768},hasTouch:true});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(()=>{
      localStorage.setItem('atem.mic','off');localStorage.setItem('jarvis.token','test');
      window.videoCounts={pause:0,play:0,destroy:0};
      window.YT={Player: class {
        constructor(node, opts) {this.node=node;window.videoEvents=opts.events;node.textContent='Player simulado para teste';setTimeout(()=>opts.events.onReady(),0);}
        destroy(){window.videoCounts.destroy++;this.node.remove();}
        pauseVideo(){window.videoCounts.pause++;window.videoEvents.onStateChange({data:2});}
        playVideo(){window.videoCounts.play++;window.videoEvents.onStateChange({data:1});}
      }};
    });
    let socket;const sent=[];
    await page.routeWebSocket('**/ws',ws=>{socket=ws;ws.onMessage(raw=>{
      const m=JSON.parse(raw);sent.push(m);
      if(m.type==='hello')ws.send(JSON.stringify({type:'welcome',state:'idle',detail:'',wake_word:'hey Luna',debug:false,memory:{available:true,count:0},wake:{available:true},llm:{available:true},tts:{available:true},transcription:{available:true}}));
    });});
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(()=>document.querySelector('#conn').textContent.includes('conectado'));
    const send=async m=>{socket.send(JSON.stringify(m));await page.waitForTimeout(60);};
    const action=async a=>send({type:'screen',action:a});
    const mode=()=>page.locator('#stage-content').getAttribute('data-mode');
    const thinking=()=>send({type:'state',state:'thinking',detail:''});
    const idle=()=>send({type:'state',state:'idle',detail:''});
    const qualityFlow = path.resolve('data/quality-flow.json');
    if (fs.existsSync(qualityFlow)) {
      const flow = JSON.parse(fs.readFileSync(qualityFlow, 'utf8'));
      await thinking();
      await send({type:'reply', text:flow.ui_websocket_text, final:true});
      await idle();
      assert.equal(await page.locator('#stage-scroll').textContent(), flow.ui_websocket_text);
      assert.equal(flow.ui_websocket_text, flow.tts_input);
      console.log('OK: saudação real do Ollama exibida integralmente, igual ao texto enviado ao TTS.');
    }
    await thinking();await send({type:'reply',text:'Uma resposta simples.',final:true});await idle();
    assert.equal(await mode(),'reply');assert.equal(await page.locator('#reply').count(),0);
    await action('settings');await action('close');assert.equal(await mode(),'reply');
    await action('home');assert.equal(await mode(),'face');
    assert.equal(await page.locator('#answer-preview').textContent(),'Uma resposta simples.');
    await page.locator('#answer-open').tap();assert.equal(await mode(),'reply');
    await page.locator('#stage-back').tap();assert.equal(await mode(),'face');
    await action('settings');await page.locator('#forecast-duration').selectOption('5');await action('close');
    const forecast={type:'weather',available:true,location:'Ouro Fino, MG',summary:'23 °C · sol',detail:'hoje, 06/11',
      updated_at:Date.now()/1000,status:'offline',stale:true,
      forecast:{text:'Amanhã, sábado, 7 de novembro de 2026: mínima de 15 e máxima de 25 graus.'}};
    await page.clock.install();
    await send(forecast);assert.equal(await mode(),'forecast');
    assert.equal(await page.locator('.weather-temperature').textContent(),'23 °C');
    assert.match(await page.locator('#weather-updated').textContent(),/sem internet/);
    assert.match(await page.locator('.forecast-day strong').textContent(),/7 de novembro/);
    await page.clock.fastForward(5100);assert.equal(await mode(),'face');
    await send(forecast);await thinking();
    await send({type:'reply',view:'recipe',text:'Panqueca\nTempo: 15 minutos\nPorções: 2\nIngredientes\n2 ovos\n200 ml de leite\nModo de preparo\n'+Array.from({length:30},(_,i)=>`${i+1}. Misture delicadamente e cozinhe.\n`).join(''),final:true});await idle();
    await page.clock.fastForward(5100);assert.equal(await mode(),'recipe');
    assert.deepEqual(await page.locator('ul.recipe-list strong').allTextContents(),['2','200 ml']);
    assert.equal(await page.locator('.recipe-steps li').last().evaluate(e=>e.value),30);
    assert.deepEqual(await page.locator('.recipe-meta').allTextContents(),['Tempo: 15 minutos','Porções: 2']);
    await action('down');assert(await page.locator('#stage-scroll').evaluate(e=>e.scrollTop>0));
    await action('up');await page.screenshot({path:'data/ui-check/stage-recipe.png'});
    await thinking();await send({type:'videos',query:'receita de pão',items:[],message:'Resultados no site oficial.'});await idle();
    assert.equal(await mode(),'results');await action('video1');assert.equal(await mode(),'results');
    assert.equal(await page.locator('input[name=search_query]').inputValue(),'receita de pão');
    await page.context().route('https://www.youtube.com/results**', route=>route.fulfill({contentType:'text/html',body:'Página oficial simulada para verificar navegação.'}));
    const popupPromise=page.context().waitForEvent('page');
    await page.getByRole('button',{name:'Pesquisar no YouTube · outra aba'}).tap();
    const popup=await popupPromise;await popup.waitForLoadState();
    assert.equal(new URL(popup.url()).searchParams.get('search_query'),'receita de pão');
    assert.equal(new URL(popup.url()).searchParams.get('hl'),'pt-BR');await popup.close();
    await page.context().setOffline(true);
    await page.getByRole('button',{name:'Pesquisar no YouTube · outra aba'}).tap();
    assert.equal(page.context().pages().length,1);assert.equal(await mode(),'results');
    await page.context().setOffline(false);
    await page.locator('input[type=url]').fill('https://evil.example/watch?v=abcdefghijk');
    await page.getByRole('button',{name:'Assistir aqui',exact:true}).tap();assert.equal(await mode(),'results');
    await page.locator('input[type=url]').fill('https://youtu.be/abcdefghijk');
    await page.getByRole('button',{name:'Assistir aqui',exact:true}).tap();assert.equal(await mode(),'video');
    await action('home');
    await page.locator('#video-search').tap();assert.equal(await mode(),'results');
    await page.locator('input[name=search_query]').pressSequentially('lasanha');assert.equal(await mode(),'results');
    await send({type:'videos',query:'pao',items:[{id:'javascript:bad',title:'Recusado',source:'falsa'},{id:'abcdefghijk',title:'Vídeo de teste <img src=x>',source:'Fonte simulada'}],message:''});
    assert.equal(await page.locator('.video-result').count(),1);assert.equal(await page.locator('#stage-scroll img').count(),0);
    await action('video1');assert.equal(await mode(),'video');
    await page.clock.runFor(50);
    await page.getByRole('button',{name:'Reproduzir',exact:true}).click();
    assert.equal(await page.locator('#stage-scroll [role=status]').textContent(),'Em reprodução');
    await page.getByRole('button',{name:'Pausar',exact:true}).click();
    assert.equal(await page.locator('#stage-scroll [role=status]').textContent(),'Pausado');
    await action('resume_video');await action('pause_video');
    assert(await page.evaluate(()=>window.videoCounts.play>0 && window.videoCounts.pause>0));
    assert(sent.some(m=>m.type==='cancel'));
    await page.evaluate(()=>window.videoEvents.onAutoplayBlocked());
    assert((await page.locator('#stage-scroll').textContent()).includes('bloqueou'));
    await page.evaluate(()=>window.videoEvents.onError());
    assert((await page.locator('#stage-scroll').textContent()).includes('Não foi possível'));
    await action('home');assert.equal(await mode(),'face');
    assert.equal(await page.evaluate(()=>window.videoCounts.destroy),2);
    assert.equal(await page.locator('#stage-scroll').textContent(),'');
    assert.deepEqual(errors,[]);
    console.log('OK: resposta, receita/rolagem, clima/prazo, troca segura, painéis, resultados validados, seleção, pausa, autoplay/erro simulados e destruição do player.');
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
