// Sample: Navigate Chromium to http://127.0.0.1:54420/, check quantity-only-v2 heading and save receipt.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(path.resolve(__dirname, '../../apps/trade-pilot/web/node_modules/playwright'));
let launchedBrowser;

(async () => {
  const browser = await chromium.launch({headless:true,channel:'chrome'});
  launchedBrowser=browser;
  const page = await browser.newPage({viewport:{width:1440,height:1100},deviceScaleFactor:1});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:54420/',{waitUntil:'networkidle'});
  await page.getByText('Recorded position',{exact:true}).waitFor();
  await page.getByRole('heading',{name:'quantity-only-v2',exact:true}).waitFor();
  const status=await (await page.request.get('http://127.0.0.1:54420/api/status')).json();
  const candidate=status.runs[0]?.checkpoint.candidateVersion??'repaired-v3';
  await page.getByRole('heading',{name:candidate,exact:true}).waitFor();
  const desktopOverflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  const candidateCard=page.locator('.candidate').filter({has:page.getByRole('heading',{name:candidate,exact:true})});
  await candidateCard.locator('details summary').click();
  const source=await (await page.request.get('http://127.0.0.1:54420/api/source?version='+encodeURIComponent(candidate))).json();
  await candidateCard.locator('pre').filter({hasText:source.source}).waitFor();
  const output=path.resolve(__dirname,'../receipts/screenshots');
  fs.mkdirSync(output,{recursive:true});
  await page.screenshot({path:path.join(output,'trading-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  const mobileOverflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  await page.screenshot({path:path.join(output,'trading-mobile.png'),fullPage:true});
  const receipt={state:errors.length||desktopOverflow||mobileOverflow?'FAIL':'PASS',
    checks:{candidateComparison:true,sourceInspection:true,desktopOverflow,mobileOverflow,pageErrors:errors},
    mutationsPerformed:false,url:'http://127.0.0.1:54420/'};
  fs.writeFileSync(path.resolve(__dirname,'../receipts/browser.json'),JSON.stringify(receipt,null,2)+'\n');
  await browser.close();
  console.log(JSON.stringify(receipt,null,2));
  if(receipt.state!=='PASS')process.exitCode=1;
})().catch(async error=>{console.error(error);if(launchedBrowser)await launchedBrowser.close();process.exitCode=1});
