const form = document.querySelector('#form');
const results = document.querySelector('#results');
const status = document.querySelector('#status');
const submit = document.querySelector('#submit');
const labels = {similarity:'相似變換',affine:'仿射變換',homography:'透視變換'};
const urls = new Map();
const samplePanel = document.querySelector('#samples');
const offlineDemo = window.OVERLAP_DEMO || null;
let selectedDemoId = null;
function sampleButtonsDisabled(disabled) {
  samplePanel.querySelectorAll('button[data-available="true"]').forEach(button => button.disabled=disabled);
}
document.querySelectorAll('.upload input').forEach(input => input.addEventListener('change', () => {
  selectedDemoId = null;
  if (urls.has(input.name)) URL.revokeObjectURL(urls.get(input.name));
  const image = input.parentElement.querySelector('img');
  image.hidden = !input.files.length;
  if (input.files.length) {
    const url = URL.createObjectURL(input.files[0]); urls.set(input.name,url); image.src=url;
    input.parentElement.querySelector('.filename').textContent=input.files[0].name;
  }
  samplePanel.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed','false'));
}));
function element(tag, text, className) {
  const node=document.createElement(tag); if(text!==undefined) node.textContent=text;
  if(className) node.className=className; return node;
}
samplePanel.querySelectorAll('button[data-sample]').forEach(button => {
  const sample=JSON.parse(button.dataset.sample);
  button.addEventListener('click',async()=>{
    sampleButtonsDisabled(true);submit.disabled=true;status.textContent='正在載入範例圖片…';
    try {
      const files=await Promise.all(['a','b'].map(async phase=>{
        const response=await fetch(sample[phase]);
        if(!response.ok) throw new Error('範例圖片載入失敗，請重新整理後再試。');
        return new File([await response.blob()],`${sample.id}-${phase}.png`,{type:'image/png'});
      }));
      ['a','b'].forEach((phase,index)=>{
        const transfer=new DataTransfer();transfer.items.add(files[index]);
        const input=form.elements[phase];input.files=transfer.files;input.dispatchEvent(new Event('change'));
      });
      form.elements.rotation_search.checked=sample.rotation_search;
      form.elements.rotation_search.disabled=Boolean(offlineDemo);
      selectedDemoId=sample.id;
      button.setAttribute('aria-pressed','true');results.replaceChildren();
      status.textContent=`已載入「${sample.title}」，可選擇特徵方法，再按「尋找對應區域」。`;
    } catch(error) {status.textContent=error.message;}
    finally {sampleButtonsDisabled(false);submit.disabled=false;}
  });
});
function show(result) {
  const card=element('section',undefined,'result'); results.append(card);
  card.append(element('h2',result.method==='sift'?'SIFT · 傳統局部特徵':'SuperPoint＋LightGlue · CNN 特徵'));
  const states={reliable:'通過幾何檢查 · 仍需目視確認',tentative:'低可信候選 · 請勿視為已匹配',no_match:'沒有可靠對應',error:'模式暫時無法執行'};
  card.append(element('span',states[result.status],'badge'));
  if(result.warning) card.append(element('p',result.warning,'warning'));
  if(!result.best) return;
  const b=result.best, metrics=element('div',undefined,'metrics');
  [[b.count,'有效匹配點'],[(100*b.ratio).toFixed(1)+'%','內點比例'],[b.median_error.toFixed(2)+' px','中位對齊誤差'],[(100*b.coverage).toFixed(1)+'%','全圖特徵覆蓋率'],[result.seconds+' s','特徵與估算耗時']].forEach(([value,label])=>{
    const m=element('div',undefined,'metric');m.append(element('strong',value),element('span',label));metrics.append(m);
  });card.append(metrics,element('p',`最佳模型：${labels[b.model]} · 配對數 ${b.src_count ?? result.matches} · 搜尋方向 ${b.rotation}° · 排序分數 ${b.score.toFixed(1)}（非機率）`));
  if(result.images.base){
    const tabs=element('div',undefined,'tabs'),viewer=element('div',undefined,'viewer');
    const base=element('img');base.alt='對齊結果'; const layer=element('img',undefined,'layer');layer.alt='變換後的 B';
    viewer.append(base,layer);
    const slider=element('label',undefined,'slider');slider.append(element('span','B 透明度'));
    const range=element('input');range.type='range';range.min='0';range.max='100';range.value='50';range.setAttribute('aria-label','B 透明度');
    range.addEventListener('input',()=>layer.style.opacity=range.value/100);slider.append(range);
    [['overlay','對齊疊圖'],['region','A 中對應範圍'],['matches','有效匹配點'],['blend','曝光補償＋羽化拼接']].forEach(([key,label],i)=>{
      const button=element('button',label,i===0?'active':'');button.type='button';
      button.addEventListener('click',()=>{
        tabs.querySelectorAll('button').forEach(n=>n.classList.remove('active'));button.classList.add('active');
        base.src=key==='overlay'?result.images.base:result.images[key];
        layer.hidden=key!=='overlay';slider.hidden=key!=='overlay';
      });tabs.append(button);
    });base.src=result.images.base;layer.src=result.images.warped;layer.style.opacity='.5';card.append(tabs,viewer,slider);
    const downloads=element('div',undefined,'downloads');
    [['blend','下載拼接預覽'],['matches','下載匹配點']].forEach(([key,label])=>{const link=element('a',label);link.href=result.images[key];link.download=`${result.method}-${key}.png`;downloads.append(link);});card.append(downloads);
  }
  const details=element('details');details.append(element('summary','候選模型與原圖座標矩陣'));
  const table=element('table');const head=element('tr');['模型','方向','內點','誤差 px','分數'].forEach(t=>head.append(element('th',t)));table.append(head);
  result.candidates.forEach(c=>{const row=element('tr');[labels[c.model],c.rotation+'°',c.count,c.median_error.toFixed(2),c.score.toFixed(1)].forEach(t=>row.append(element('td',t)));table.append(row);});
  details.append(table,element('p','矩陣方向：EXIF 校正後的原圖 B → A。像素誤差以縮小後圖片計算。'),element('pre',JSON.stringify(result.matrix_original,null,2)));card.append(details);
}
form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;results.replaceChildren();status.textContent='正在提取特徵並尋找幾何一致的對應… CNN 四方向搜尋可能需要較長時間。';
  const data=new FormData(form);data.set('rotation_search',String(form.elements.rotation_search.checked));
  sampleButtonsDisabled(true);
  try {
    let payload;
    if(offlineDemo) {
      if(!selectedDemoId) throw new Error('這是離線範例展示檔。請先選上方照片組；自行上傳的圖片需透過 Python 網站版計算。');
      const saved=offlineDemo[selectedDemoId];
      const method=form.elements.method.value;
      payload={...saved,results:saved.results.filter(result=>method==='compare'||result.method===method)};
    } else {
      const response=await fetch(form.action,{method:'POST',body:data});payload=await response.json();
      if(!response.ok) throw new Error(payload.error||'處理失敗');
    }
    payload.results.forEach(show);status.textContent=`處理完成。A ${payload.working_sizes.a.join(' × ')}；B ${payload.working_sizes.b.join(' × ')}。分數僅供候選排序，請檢查疊圖。`;
    if(offlineDemo) status.textContent='範例已計算結果 · '+status.textContent;
  }catch(error){status.textContent=error.message;}finally{submit.disabled=false;sampleButtonsDisabled(false);}
});
