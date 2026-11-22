'use strict';

const $ = selector => document.querySelector(selector);
const state = {data:null, topic:null, detail:null, view:'overview', request:0};

function el(tag, attrs={}, ...children) {
  const node = document.createElement(tag);
  for (const [key,value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function date(value, includeTime=false) {
  if (!value) return 'Ещё не проверялось';
  const stamp = new Date(value);
  if (Number.isNaN(stamp.getTime())) return value;
  return stamp.toLocaleDateString('ru-RU',{day:'numeric',month:'short',year:'numeric', ...(includeTime ? {hour:'2-digit',minute:'2-digit'} : {})});
}

function safeURL(value) {
  try { const url = new URL(value); return ['https:','http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}

async function readData(path) {
  const response = await fetch(path,{cache:'no-cache'});
  if (!response.ok) throw new Error('Не удалось загрузить опубликованные данные.');
  return response.json();
}

function selected() { return state.data?.topics.find(topic=>topic.id === state.topic); }

function renderHeader() {
  const topic = selected();
  $('#topic-kicker').textContent = topic.name;
  $('#topic-title').textContent = topic.reader_title || topic.topic;
  $('#topic-description').textContent = topic.description;
  $('#review-status').textContent = topic.has_review ? 'Обзор опубликован' : 'Есть только библиография';
  $('#cadence').textContent = topic.has_review ? topic.review_papers+' разобранных работ' : topic.papers+' кандидатов';
  $('#last-updated').textContent = topic.updated_at ? 'Обзор: '+date(topic.updated_at) : '';
  $('#source-count').textContent = topic.papers;
  document.title = `${topic.reader_title || topic.topic} · Research Atlas`;
}

function renderHome(archive=false) {
  ++state.request; state.topic=null; state.detail=null; window.scrollTo(0,0);
  $('#reader').hidden=true; $('#home-panel').hidden=false;
  document.title = archive ? 'Архив · Research Atlas' : 'Research Atlas — исследовательские разборы';
  const topics = state.data.topics.filter(t=>archive ? !t.focus : t.focus);
  const heading = el('section',{class:'home-intro'},
    el('h1',{},archive ? 'Другие обзоры' : 'ИИ: самостоятельность человека\nи общая память агентов'),
    el('p',{class:'hero-description'},archive ? 'Разборы отдельных вопросов безопасности, интерпретируемости, эффективности, робототехники и других направлений AI.' : 'Как люди принимают решения с помощью ИИ и как агенты накапливают общий опыт. В каждом разделе — объяснение механизмов, сравнение исследований и ссылки на первоисточники.'));
  const cards = el('div',{class:'topic-cards'+(archive?' archive-cards':'')},topics.map(topic=>el('a',{class:'topic-card',href:'#'+topic.id},
    el('p',{class:'eyebrow'},topic.name),el('h2',{},topic.reader_title || topic.topic),
    el('p',{},topic.description),topic.reader_goals ? el('ul',{class:'reader-goals'},topic.reader_goals.map(text=>el('li',{},text))) : null,
    el('div',{class:'card-meta'},el('span',{},topic.review_papers+' разобранных работ'),el('span',{},topic.has_review ? 'Открыть обзор' : 'Открыть тему')))));
  const method = archive ? el('a',{class:'back-link',href:'#home'},'Основные разделы') : el('section',{class:'reading-guide'},
    el('h2',{},'Как читать'),el('div',{class:'guide-grid'},
      ...[['Краткий ответ','Откройте тему и начните с вывода. Для объяснений и таблицы работ включите подробный разбор.'],['Карта вопроса','Посмотрите, как связаны механизмы, подходы, ограничения и открытые вопросы.'],['Первоисточники','Нажмите [1] рядом с утверждением, чтобы увидеть цитату и перейти к нужному разделу статьи.']].map(([title,text])=>el('div',{},el('h3',{},title),el('p',{},text)))));
  $('#home-panel').replaceChildren(heading,cards,method);
}

function showSection(key) {
  state.view='overview'; renderPanel(); setReadingLevel('detail');
  document.getElementById('section-'+key)?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
}

function renderQuestionMap(review) {
  if (!review) return emptyPanel('','Пока нет разбора.','Карта появится после чтения источников.');
  const papers=review.papers, claims=new Map(review.cards.flatMap(c=>c.claims.map(item=>[item.id,item])));
  const wrap=el('section',{class:'question-map'},el('h2',{},'Как устроен исследовательский вопрос'),
    el('p',{class:'map-intro'},'Редакционная схема: какие подходы отвечают на вопрос, где заканчиваются доказательства и что проверить дальше.'));
  const stages=[['mechanism','Что именно изучаем'],['approaches','Какие есть подходы'],['disagreements','Где вывод ограничен'],['questions','Что проверить дальше']];
  const grid=el('div',{class:'map-grid'});
  for(const [key,title] of stages) {
    const section=review.synthesis.sections.find(s=>s.key===key); if(!section) continue;
    grid.append(el('section',{class:'map-stage'},el('h3',{},title),el('div',{class:'map-item'},scientificItem(section.items[0],claims,papers)),section.items.length>1 ? el('details',{},el('summary',{},'Ещё '+(section.items.length-1)+' пункта'),section.items.slice(1).map(item=>scientificItem(item,claims,papers))) : null,el('button',{class:'text-button',onClick:()=>showSection(key)},'Открыть в разборе')));
  }
  wrap.append(grid,el('p',{class:'review-notice'},'Следующие проверки — предложения редакции. Они ещё не выполнены и не являются результатами исследований.'));
  return wrap;
}

function citation(claimId, claims, papers) {
  const claim = claims.get(claimId), number = claim ? papers.findIndex(p=>p.id === claim.paper_id)+1 : 0;
  if (!claim || !number) return el('span',{class:'review-notice'},'Ссылка требует проверки');
  return el('button',{class:'citation',title:papers[number-1].title,'aria-label':'Показать доказательство: '+papers[number-1].title,onClick:()=>{
    if (state.view !== 'overview') {state.view='overview';renderPanel();}
    const target = document.getElementById('claim-'+claimId);
    if (!target) return;
    setReadingLevel('detail');
    const details = target.closest('details'); if (details) details.open = true;
    target.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});
    target.focus({preventScroll:true});
  }},'['+number+']');
}

function setReadingLevel(level) {
  document.querySelector('.review-article')?.classList.toggle('brief-mode',level === 'brief');
  document.querySelectorAll('[data-reading-level]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.readingLevel === level)));
}

function comparisonTable(review, claims, papers) {
  const groups=[...new Set(review.comparison.map(row=>row.theme).filter(Boolean))];
  const filter=el('input',{type:'search',placeholder:'Название, метод или условие','aria-label':'Поиск в сравнении работ'});
  const group=el('select',{'aria-label':'Подтема в сравнении'},el('option',{value:''},'Все подтемы'),groups.map(name=>el('option',{value:name},name)));
  const summary=el('p',{class:'source-summary','aria-live':'polite'}),body=el('tbody');
  function render() {
    const query=filter.value.toLocaleLowerCase();
    const rows=review.comparison.filter(row=>(!group.value || row.theme===group.value) && [papers.find(p=>p.id===row.paper_id).title,row.method,row.guarantee,row.setup,row.theme,row.study_type].join(' ').toLocaleLowerCase().includes(query));
    summary.textContent='Работ в сравнении: '+rows.length+' из '+review.comparison.length;
    body.replaceChildren(...rows.map(row=>el('tr',{},el('th',{scope:'row'},papers.find(p=>p.id===row.paper_id).title,
      row.study_type ? el('p',{class:'study-type'},row.study_type+' · '+row.theme) : null,row.claim_ids.map(id=>citation(id,claims,papers))),
      ...['method','guarantee','setup'].map(key=>el('td',{},row[key])))));
    if(!rows.length)body.append(el('tr',{},el('td',{colspan:'4'},'Нет работ с такими условиями поиска.')));
  }
  filter.addEventListener('input',render);group.addEventListener('change',render);render();
  return el('section',{},el('div',{class:'source-filters'},filter,groups.length ? group : null),summary,
    el('div',{class:'comparison-scroll',tabindex:'0','aria-label':'Сравнение исследований; таблицу можно прокручивать'},
      el('table',{class:'comparison-table'},el('caption',{},'Подходы, условия и границы выводов из публикаций'),
        el('thead',{},el('tr',{},['Работа и тип исследования','Подход','Вывод и его границы','Дизайн проверки'].map(title=>el('th',{scope:'col'},title)))),body)));
}

function scientificItem(item, claims, papers) {
  return el('p',{},item.kind === 'editorial' ? el('span',{class:'editorial'},'Комментарий к обзору · ') : null,
    item.text, item.claim_ids.map(id=>citation(id,claims,papers)));
}

function renderReview(review, topic) {
  const papers = review.papers, claims = new Map(review.cards.flatMap(card=>card.claims.map(claim=>[claim.id,claim])));
  const layered = review.synthesis.sections.some(section=>section.key==='brief');
  const article = el('article',{class:'review-article'+(layered?' brief-mode':'')},el('div',{class:'review-notice'},'Разбор выбранных источников. Независимая экспертная рецензия и собственное воспроизведение экспериментов не выполнялись.'));
  if (layered) article.append(el('div',{class:'reading-levels','aria-label':'Глубина чтения'},
    ...[['brief','За минуту'],['detail','Подробный разбор и таблица']].map(([level,label])=>el('button',{'data-reading-level':level,'aria-pressed':String(level==='brief'),onClick:()=>setReadingLevel(level)},label))));
  for (const section of review.synthesis.sections) article.append(el('section',{class:'review-section','data-key':section.key,id:'section-'+section.key},el('h2',{},section.title),section.items.map(item=>scientificItem(item,claims,papers)),
    section.key==='comparison' && review.comparison ? comparisonTable(review,claims,papers) : null));
  const evidencePanel = el('section',{class:'evidence-panel'},el('h2',{},'Доказательства и цитаты'));
  for (const card of review.cards) {
    const paper = papers.find(p=>p.id === card.paper_id);
    const items = card.claims.map(claim=>el('li',{class:'claim',id:'claim-'+claim.id,tabindex:'-1'},el('small',{},'Выбранные разделы источника'),el('p',{},claim.text),
      claim.conditions.length ? el('p',{},'Условия: '+claim.conditions.join('; ')) : null,
      claim.limitations.length ? el('p',{},'Ограничения и оговорки: '+claim.limitations.join('; ')) : null,
      claim.evidence.map(evidence=>el('div',{},el('blockquote',{},evidence.quote),el('small',{},safeURL(evidence.url) ? el('a',{href:safeURL(evidence.url),target:'_blank',rel:'noreferrer'},evidence.locator+' · открыть первоисточник ↗') : 'Локатор: '+evidence.segment_id)))));
    evidencePanel.append(el('details',{},el('summary',{},paper?.title || card.paper_id),el('ul',{class:'claims-list'},items)));
  }
  article.append(evidencePanel);
  if (review.quality.warnings?.length) article.append(el('details',{class:'review-limits'},el('summary',{},'Границы и статус проверки обзора'),review.quality.warnings.map(w=>el('p',{},w))));
  const sidebar = el('aside',{class:'review-sidebar'},el('div',{class:'section-nav'},el('div',{class:'panel-kicker'},`СТАТЬИ: ${papers.length} · ПОНЯТИЯ: ${review.synthesis.concepts.length}`),review.synthesis.sections.map(section=>el('a',{href:'#section-'+section.key,onClick:event=>{event.preventDefault();setReadingLevel('detail');document.getElementById('section-'+section.key)?.scrollIntoView({behavior:'smooth'});}},section.title)),el('div',{class:'panel-kicker'},'КАРТОЧКИ ПОНЯТИЙ'),el('div',{class:'concept-list'},review.synthesis.concepts.map(concept=>el('details',{},el('summary',{},concept.name),scientificItem(concept.definition,claims,papers),concept.statements.map(item=>scientificItem(item,claims,papers)))))));
  const base = './exports/'+topic.id+'/'+review.snapshot_id+'/';
  sidebar.append(el('div',{class:'review-downloads'},el('a',{href:base+'index.html',target:'_blank',rel:'noreferrer'},'Отдельная страница ↗'),el('a',{href:base+'references.bib',download:'references.bib'},'BibTeX ↓')));
  return el('div',{class:'review-layout'},article,sidebar);
}

function emptyPanel(symbol,title,description,button) {
  return el('section',{class:'empty-panel'},el('span',{class:'empty-icon','aria-hidden':'true'},symbol),el('h2',{},title),el('p',{},description),button);
}

const discoveryLabels = {relevance:'Методы и постановка задачи',recent:'Свежие публикации',earlier:'Ранние работы',evaluation:'Оценка и ограничения',seed:'Исходная подборка'};

function coveragePanel(topic) {
  const c = topic.bibliography_coverage;
  if (!c?.count) return null;
  return el('section',{class:'coverage-panel'},el('div',{class:'panel-kicker'},'ОХВАТ ПОИСКОВОГО КОРПУСА'),
    topic.discovery ? el('p',{},'Последняя проверка: '+date(topic.bibliography_checked_at)+' · Новых кандидатов: '+topic.discovery.added+'; новых версий: '+topic.discovery.updated+'. Выводы обзора автоматически не менялись.') : null,
    el('h3',{},c.count+' записей · '+c.first_year+'–'+c.last_year),
    el('p',{},'Годы: '+Object.entries(c.years).map(([year,count])=>year+': '+count).join(' · ')),
    el('p',{},Object.entries(c.groups).map(([key,count])=>discoveryLabels[key]+': '+count).join(' · ')),
    c.searches ? el('p',{},'Поисковых стратегий: '+c.searches+'. Усечённых выдач: '+c.truncated_searches+'. Дополнительные совпадения остались за пределами этого корпуса.') : null,
    el('p',{},'Одна работа может попасть в несколько поисковых групп. Для этих дополнительных кандидатов проверены метаданные; полные тексты ещё не разобраны. Полнота охвата не установлена.'),
    el('a',{href:'./data/search-method.json',target:'_blank',rel:'noreferrer'},'Протокол поиска ↗'));
}

function renderSources(topic) {
  const papers = state.detail.papers;
  if (!papers.length) return emptyPanel('≋','Источники появятся вместе с обзором.','Поиск и подготовка выполняются редакцией отдельно от сайта.');
  const list = el('ul',{class:'source-list'}), wrap = el('section');
  const search = el('input',{type:'search',placeholder:'Название или автор','aria-label':'Фильтр опубликованных источников'});
  const years = [...new Set(papers.map(p=>p.year).filter(Boolean))].sort((a,b)=>b-a);
  const year = el('select',{'aria-label':'Год публикации'},el('option',{value:''},'Все годы'),years.map(y=>el('option',{value:y},y)));
  const group = el('select',{'aria-label':'Поисковая группа'},el('option',{value:''},'Все группы'),Object.entries(discoveryLabels).map(([key,label])=>el('option',{value:key},label)));
  const taxonomy=new Map((state.detail.review?.comparison||[]).map(row=>[row.paper_id,row]));
  const themes=[...new Set([...taxonomy.values()].map(row=>row.theme).filter(Boolean))];
  const theme=el('select',{'aria-label':'Подтема источников'},el('option',{value:''},'Все подтемы'),themes.map(t=>el('option',{value:t},t)));
  const status = el('select',{'aria-label':'Статус разбора источника'},el('option',{value:'reviewed'},'Разобранные работы'),el('option',{value:'candidates'},'Кандидаты'),el('option',{value:'all'},'Все записи'));
  if (!state.detail.review) status.value='all';
  const order = el('select',{'aria-label':'Порядок источников'},el('option',{value:'newest'},'Сначала новые'),el('option',{value:'oldest'},'Сначала ранние'));
  const summary = el('span',{class:'source-summary'}), more = el('button',{class:'button secondary',type:'button'},'Показать ещё');
  const labels = {core:'Основной корпус',external:'Внешняя работа',reading_list:'Кандидат · метаданные проверены'};
  let visible = 25;
  function render() {
    const query = search.value.toLocaleLowerCase();
    const shown = papers.filter(p=>(p.title+' '+(p.authors||[]).join(' ')).toLocaleLowerCase().includes(query)
      && (!theme.value || taxonomy.get(p.id)?.theme===theme.value)
      && (status.value==='all' || (status.value==='candidates' ? p.decision==='reading_list' : p.decision!=='reading_list'))
      && (!year.value || String(p.year)===year.value) && (!group.value || (p.discovery_groups||[]).includes(group.value)))
      .sort((a,b)=>order.value==='oldest' ? (a.published_at||String(a.year)).localeCompare(b.published_at||String(b.year)) : (b.published_at||String(b.year)).localeCompare(a.published_at||String(a.year)));
    summary.textContent = `Показано ${Math.min(visible,shown.length)} из ${shown.length} · всего в разделе ${papers.length}`;
    list.replaceChildren(...shown.slice(0,visible).map(paper=>{
      const url = safeURL(paper.url), title = url ? el('a',{href:url,target:'_blank',rel:'noreferrer'},paper.title+' ↗') : paper.title;
      return el('li',{class:'source-card'},el('div',{class:'source-meta'},paper.published_at ? date(paper.published_at) : paper.year||'Год не указан',el('span',{class:'source-status'},labels[paper.decision]||paper.decision)),el('h3',{},title),taxonomy.get(paper.id)?.study_type ? el('p',{class:'study-type'},taxonomy.get(paper.id).study_type+' · '+taxonomy.get(paper.id).theme) : null,el('p',{},(paper.authors||[]).join(', ')),paper.arxiv_id ? el('p',{},'arXiv: '+paper.arxiv_id+(paper.updated_at ? ' · версия '+date(paper.updated_at) : '')) : null,
        paper.discovery_groups?.length ? el('p',{},'Найдено: '+paper.discovery_groups.map(key=>discoveryLabels[key]||key).join(' · ')) : null,
        paper.added_at ? el('p',{},'Добавлено в каталог: '+date(paper.added_at)) : null,
        paper.available_arxiv_id ? el('p',{class:'review-notice'},'Обзор использует '+paper.arxiv_id+'. Доступна более новая версия: ',el('a',{href:'https://arxiv.org/abs/'+paper.available_arxiv_id,target:'_blank',rel:'noreferrer'},paper.available_arxiv_id+' ↗')) : null);
    }));
    if (!shown.length) list.append(el('li',{class:'empty-panel'},el('p',{},'Нет совпадений с выбранными фильтрами.')));
    more.hidden = visible >= shown.length;
  }
  for (const control of [search,year,group,order,status,theme]) control.addEventListener(control===search?'input':'change',()=>{visible=25;render();});
  more.addEventListener('click',()=>{visible+=25;render();});
  if (papers.some(p=>p.decision==='reading_list')) wrap.append(el('details',{},el('summary',{},'Автоматический поиск: охват и ограничения'),coveragePanel(topic)),el('p',{class:'review-notice'},(topic.has_review ? topic.review_papers+' работ разобраны в опубликованном обзоре; остальные записи — дополнительные кандидаты. ' : 'Поисковый корпус для будущего обзора. ')+'Включение в выдачу не подтверждает научную значимость или качество работы. Дата поиска: '+date(topic.bibliography_checked_at)));
  wrap.append(el('div',{class:'source-filters'},search,status,themes.length ? theme : null,year,group,order),el('div',{class:'source-toolbar'},summary,el('a',{href:'./exports/reading-lists/'+topic.id+'/references.bib',download:'references.bib'},'Скачать BibTeX ↓')),list,more);
  render();
  return wrap;
}

function renderHistory(topic) {
  if (!state.detail.versions.length) return emptyPanel('◷','Выпусков пока нет.','История появится после первой публикации. В ней будут только выпущенные версии и объяснение того, что изменилось.');
  const list = el('ol',{class:'history-list'});
  state.detail.versions.forEach(version=>{
    const stamp = version.released_at;
    list.append(el('li',{},el('span',{class:'history-date'},date(stamp)),el('div',{class:'history-copy'},el('strong',{},version.id === topic.snapshot_id ? 'Текущая версия' : 'Сохранённая версия'),el('p',{},version.note)),
      el('a',{href:'./exports/'+topic.id+'/'+version.id+'/index.html',target:'_blank',rel:'noreferrer'},'Открыть ↗')));
  });
  return list;
}

function renderPanel() {
  const panel = $('#view-panel'), topic = selected();
  document.querySelectorAll('[role=tab]').forEach(button=> {const active=button.dataset.view===state.view;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;});
  panel.setAttribute('aria-labelledby','tab-'+state.view);
  if (!state.detail) {panel.replaceChildren(el('div',{class:'loading'},'Загружаем данные темы…'));return;}
  panel.replaceChildren(state.view === 'map' ? renderQuestionMap(state.detail.review) : state.view === 'sources' ? renderSources(topic) : state.view === 'history' ? renderHistory(topic) : state.detail.review ? renderReview(state.detail.review,topic) : emptyPanel('','Обзор ещё не опубликован.','Доступные кандидаты находятся во вкладке «Источники».'));
}

async function selectTopic(id) {
  if (!state.data.topics.some(topic=>topic.id===id)) id=state.data.topics[0].id;
  const request = ++state.request; state.topic=id;state.detail=null;state.view='overview'; window.scrollTo(0,0);
  $('#home-panel').hidden=true;$('#reader').hidden=false;renderHeader();renderPanel();
  try {const detail = await readData('./data/topics/'+id+'.json');if (request!==state.request) return;state.detail=detail;renderPanel();}
  catch(error) {if (request===state.request) $('#view-panel').replaceChildren(emptyPanel('!','Не удалось прочитать тему.',error.message));}
}

$('.skip').addEventListener('click',event=>{event.preventDefault();$('#content').focus();});
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.close).close()));
$('#method-button').addEventListener('click',()=>$('#method-dialog').showModal());
function route() {
  const id=location.hash.slice(1);
  if (!state.data) return;
  if (!id || id==='home' || id==='content' || id==='archive') renderHome(id==='archive');
  else if(state.data.topics.some(t=>t.id===id)) selectTopic(id);
  else {renderHome();$('#home-panel').prepend(el('p',{role:'status',class:'review-notice'},'Тема по этой ссылке не найдена. Выберите опубликованный разбор.'));}
}
window.addEventListener('hashchange',route);
document.querySelectorAll('[role=tab]').forEach(button=>button.addEventListener('click',()=>{state.view=button.dataset.view;renderPanel();}));
$('.tabs').addEventListener('keydown',event=>{
  const tabs=[...document.querySelectorAll('[role=tab]')];let index=tabs.indexOf(document.activeElement);
  if (!['ArrowRight','ArrowLeft','Home','End'].includes(event.key)||index<0) return;
  event.preventDefault();index=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
  tabs[index].click();tabs[index].focus();
});
async function boot() {
  try {
    state.data=await readData('./data/library.json');
    route();
  } catch(error) {
    $('#reader').hidden=true;$('#home-panel').hidden=false;$('#home-panel').replaceChildren(el('section',{class:'offline-error'},el('h2',{},'Библиотека временно недоступна.'),el('p',{},'Попробуйте обновить страницу. Возможно, статические файлы ещё не размещены полностью.'),el('p',{},error.message)));
  }
}
boot();
