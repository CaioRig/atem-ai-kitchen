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
        pauseVideo(){window.videoCounts.pause++;}
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
    await thinking();await send({type:'reply',text:'Uma resposta simples.',final:true});await idle();
    assert.equal(await mode(),'reply');assert.equal(await page.locator('#reply').count(),0);
    await action('settings');await action('close');assert.equal(await mode(),'reply');
    await action('home');assert.equal(await mode(),'face');
    await action('settings');await page.locator('#forecast-duration').selectOption('5');await action('close');
    const forecast={type:'weather',available:true,summary:'Hoje: sol',detail:'',forecast:{text:'Amanhã: mínima 15, máxima 25 graus e chance de chuva 30%.'}};
    await page.clock.install();
    await send(forecast);assert.equal(await mode(),'forecast');
    await page.clock.fastForward(5100);assert.equal(await mode(),'face');
    await send(forecast);await thinking();
    await send({type:'reply',view:'recipe',text:'Panqueca\nIngredientes\n2 ovos\n200 ml de leite\nModo de preparo\n'+('1. Misture delicadamente e cozinhe.\n').repeat(30),final:true});await idle();
    await page.clock.fastForward(5100);assert.equal(await mode(),'recipe');
    await action('down');assert(await page.locator('#stage-scroll').evaluate(e=>e.scrollTop>0));
    await action('up');await page.screenshot({path:'data/ui-check/stage-recipe.png'});
    await thinking();await send({type:'videos',query:'receita de pão',items:[],message:'Busca não configurada.'});await idle();
    assert.equal(await mode(),'results');await action('video1');assert.equal(await mode(),'results');
    assert((await page.locator('.external-video').getAttribute('href')).startsWith('https://www.youtube.com/results?'));
    await send({type:'videos',query:'pao',items:[{id:'javascript:bad',title:'Recusado',source:'falsa'},{id:'abcdefghijk',title:'Vídeo de teste <img src=x>',source:'Fonte simulada'}],message:''});
    assert.equal(await page.locator('.video-result').count(),1);assert.equal(await page.locator('#stage-scroll img').count(),0);
    await action('video1');assert.equal(await mode(),'video');
    await page.clock.runFor(50);await action('resume_video');await action('pause_video');
    assert(await page.evaluate(()=>window.videoCounts.play>0 && window.videoCounts.pause>0));
    assert(sent.some(m=>m.type==='cancel'));
    await page.evaluate(()=>window.videoEvents.onAutoplayBlocked());
    assert((await page.locator('#stage-scroll').textContent()).includes('bloqueou'));
    await page.evaluate(()=>window.videoEvents.onError());
    assert((await page.locator('#stage-scroll').textContent()).includes('Não foi possível'));
    await action('home');assert.equal(await mode(),'face');
    assert.equal(await page.evaluate(()=>window.videoCounts.destroy),1);
    assert.equal(await page.locator('#stage-scroll').textContent(),'');
    assert.deepEqual(errors,[]);
    console.log('OK: resposta, receita/rolagem, clima/prazo, troca segura, painéis, resultados validados, seleção, pausa, autoplay/erro simulados e destruição do player.');
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
