// Exercise the generated application's three business surfaces on loopback.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(path.resolve(__dirname, '../../apps/trade-pilot/web/node_modules/playwright'));

async function main() {
  // Sample: Book 2 lots of 100, remove 1 lot, then check the mobile position table.
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  try {
    const page = await browser.newPage({viewport:{width:1400,height:1000}});
    const errors=[];
    page.on('pageerror',function recordError(error){
      // Sample: Capture a failed UI script. Keep its message in the proof receipt.
      errors.push(error.message);
    });
    await page.goto('http://127.0.0.1:54430/',{waitUntil:'networkidle'});
    await page.getByRole('heading',{name:'Exchange positions',exact:true}).waitFor();
    await page.getByRole('button',{name:'Book trade',exact:true}).click();
    await page.locator('#net').filter({hasText:'200 shares'}).waitFor();
    await page.getByRole('button',{name:'Apply correction',exact:true}).click();
    await page.locator('#net').filter({hasText:'100 shares'}).waitFor();
    await page.getByRole('button',{name:'Book trade',exact:true}).click();
    await page.locator('#message').filter({hasText:'Trade ID already exists'}).waitFor();
    const desktopOverflow = await page.evaluate(function desktopWidth(){
      // Sample: Compare the document width with the desktop viewport. Expect no overflow.
      return document.documentElement.scrollWidth>innerWidth;
    });
    const directory=path.resolve(__dirname,'../receipts/screenshots');fs.mkdirSync(directory,{recursive:true});
    await page.screenshot({path:path.join(directory,'generated-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    const mobileOverflow = await page.evaluate(function mobileWidth(){
      // Sample: Compare the document width with the mobile viewport. Expect no overflow.
      return document.documentElement.scrollWidth>innerWidth;
    });
    await page.screenshot({path:path.join(directory,'generated-mobile.png'),fullPage:true});
    const response = await page.request.post('http://127.0.0.1:54430/commands',{
      headers:{Origin:'http://untrusted.invalid'},data:{action:'book',id:'bad',lots:1,lot_size:100}});
    const value={state:errors.length||desktopOverflow||mobileOverflow||response.status()!==403?'FAIL':'PASS',
      checks:{openingShares:200,correctionShares:100,duplicateRejected:true,crossOriginDenied:response.status()===403,
              desktopOverflow,mobileOverflow,pageErrors:errors},scope:'Generated synthetic app only; no production effects'};
    fs.writeFileSync(path.resolve(__dirname,'../receipts/generated-ui.json'),JSON.stringify(value,null,2)+'\n');
    console.log(JSON.stringify(value));
    if(value.state!=='PASS')process.exitCode=1;
  } finally {await browser.close();}
}
main().catch(function fail(error){
  // Sample: Report a failed browser assertion. Exit with an unsuccessful proof status.
  console.error(error.message);process.exitCode=1;
});
