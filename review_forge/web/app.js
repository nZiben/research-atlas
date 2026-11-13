'use strict';

const $ = selector => document.querySelector(selector);
const display = {
  safety: {short:'Safety & alignment', lines:['AI Safety', '& Alignment'], label:'БЕЗОПАСНОСТЬ И СОГЛАСОВАНИЕ', icon:'M12 3 4.5 6v5c0 4.5 3 7.5 7.5 10 4.5-2.5 7.5-5.5 7.5-10V6L12 3Z M8.5 11.5l2.5 2.5 4.5-5'},
  interpretability: {short:'Интерпретируемость', lines:['Интерпретируемость', 'и теория'], label:'МЕХАНИЗМЫ И ОБОБЩЕНИЕ', icon:'M3 12h4l3-8 4 16 3-8h4'},
  efficiency: {short:'Эффективность AI', lines:['Вычислять', 'эффективнее.'], label:'ОБУЧЕНИЕ И ИНФЕРЕНС', icon:'m13 2-9 12h7l-1 8 10-12h-7l0-8Z'},
  language: {short:'LLM & NLP', lines:['Язык. Контекст.', 'Рассуждение.'], label:'ЯЗЫКОВЫЕ МОДЕЛИ И СПОСОБНОСТИ', icon:'M4 4h16v12H9l-5 4V4Z M8 8h8M8 12h5'},
  agents: {short:'Агенты & RAG', lines:['Агенты, память', 'и инструменты.'], label:'ПОИСК ЗНАНИЙ И ДЕЙСТВИЯ', icon:'M8 4h8v5H8V4Z M3 16h6v5H3v-5Z M15 16h6v5h-6v-5Z M12 9v4M6 16v-3h12v3'},
  vision: {short:'Vision & multimodal', lines:['Видеть.', 'Связывать. Понимать.'], label:'ИЗОБРАЖЕНИЯ, ВИДЕО И МУЛЬТИМОДАЛЬНОСТЬ', icon:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z'},
  robotics: {short:'Robotics & embodied AI', lines:['Интеллект', 'в физическом мире.'], label:'РОБОТЫ И ВОПЛОЩЁННЫЕ АГЕНТЫ', icon:'M5 8h14v12H5V8Z M12 3v5M9 12v2M15 12v2M9 17h6M2 11v6M22 11v6'},
  'reinforcement-learning': {short:'Reinforcement learning', lines:['Обучение', 'через действие.'], label:'ВОЗНАГРАЖДЕНИЕ И ПРИНЯТИЕ РЕШЕНИЙ', icon:'M5 6h10l-3-3M19 18H9l3 3M18 6a7 7 0 0 1 1 10M6 18a7 7 0 0 1-1-10'},
  evaluation: {short:'Оценка & данные', lines:['Измерять точно.', 'Проверять выводы.'], label:'ДАННЫЕ И ВОСПРОИЗВОДИМОСТЬ', icon:'M5 20V10M12 20V4M19 20v-7M3 20h18M3 6l3 3 5-6'},
  'ai-for-science': {short:'AI for Science', lines:['Модели', 'для открытий.'], label:'НАУКА И ИСКУССТВЕННЫЙ ИНТЕЛЛЕКТ', icon:'M9 3h6M10 3v6L4 19a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2L14 9V3M7 15h10'},
};
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

function icon(path) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('class','topic-symbol'); svg.setAttribute('aria-hidden','true');
  const shape = document.createElementNS(svg.namespaceURI,'path'); shape.setAttribute('d',path); svg.append(shape); return svg;
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

function renderNav() {
  const nav = $('#topic-nav'); nav.replaceChildren();
  state.data.topics.forEach((topic,index)=> {
    const item = el('a',{class:'topic-link' + (topic.id === state.topic ? ' active':''), href:'#'+topic.id,
      'aria-current':topic.id === state.topic ? 'page':null}, icon(display[topic.id].icon), display[topic.id].short,
      el('span',{class:'topic-number'},topic.has_review ? 'Читать' : String(index+1).padStart(2,'0')));
    nav.append(item);
  });
  $('#library-count').textContent = 'Обзоров: '+state.data.topics.filter(topic=>topic.has_review).length;
  if (window.matchMedia('(max-width: 640px)').matches) nav.querySelector('.active')?.scrollIntoView({block:'nearest',inline:'center'});
}

function renderHeader() {
  const topic = selected(), shown = display[topic.id], index = state.data.topics.findIndex(t=>t.id === topic.id)+1;
  $('#breadcrumb-topic').textContent = shown.short;
  $('#topic-kicker').textContent = String(index).padStart(2,'0')+' / '+shown.label;
  $('#topic-title').replaceChildren(...shown.lines.flatMap((line,i)=>i ? [el('br'),document.createTextNode(line)] : [document.createTextNode(line)]));
  $('#topic-description').textContent = topic.description;
  $('#reading-actions').hidden = !topic.has_review && !topic.papers;
  $('#read-overview').hidden = !topic.has_review;
  $('#read-sources').hidden = !topic.papers;
  $('#action-note').textContent = topic.has_review ? 'От выводов можно перейти к тезисам и первоисточникам.' : 'Библиография проверена по arXiv. Полный научный разбор ещё готовится.';
  $('#review-status').textContent = topic.has_review ? 'Редакционный выпуск' : topic.papers ? 'Библиография доступна' : 'Раздел готовится';
  $('#review-status').className = 'pill ' + (topic.has_review ? 'ready':'');
  $('#cadence').textContent = topic.has_review ? topic.review_papers+' разобранных работ · версия в истории' : topic.papers ? topic.papers+' источников · обзор готовится' : 'Материалы ещё не опубликованы';
  $('#last-updated').textContent = topic.updated_at ? 'Опубликовано: '+date(topic.updated_at) : topic.bibliography_checked_at ? 'Поиск: '+date(topic.bibliography_checked_at) : '';
  $('#source-count').textContent = topic.papers;
  document.title = `${shown.short} — Review Forge`;
}

function methodNote() {
  const note = el('aside',{class:'side-note'},el('div',{class:'panel-kicker'},'НЕСКОЛЬКО СЛОЁВ ПОНИМАНИЯ'),el('h3',{},'Больше, чем список статей.'));
  [['Понятия и механизмы','Определения, подходы и связи между идеями.'],['Первичные свидетельства','Тезисы, условия, короткие цитаты и точные ссылки.'],['Контекст и ограничения','Где работы согласуются, расходятся и оставляют вопросы.']].forEach(([title,description],i)=>note.append(el('div',{class:'method-step'},el('span',{class:'step-number'},'0'+(i+1)),el('div',{},el('strong',{},title),el('p',{},description)))));
  note.append(el('p',{class:'side-footnote'},el('strong',{},'Обновлять по делу.'),' Новые публикации сначала проходят отбор. Стабильные объяснения не нужно переписывать каждую неделю.'));
  return note;
}

function renderEmptyOverview(topic) {
  const card = el('section',{class:'focus-card'},el('div',{class:'focus-body'},
    el('div',{class:'focus-heading'},el('div',{class:'panel-kicker'},'ПЛАНИРУЕМЫЙ ФОКУС'),el('span',{class:'tiny-label'},'ВОПРОС → ИССЛЕДОВАНИЕ')),
    el('h2',{},topic.topic),el('p',{class:'question-heading'},'Исследовательские вопросы раздела'),
    el('ol',{class:'questions'},topic.questions.map(question=>el('li',{},question)))),
    el('div',{class:'focus-bottom'},el('span',{class:'empty-mark','aria-hidden':'true'},'≋'),el('div',{},el('strong',{},topic.papers ? 'Источников в корпусе: '+topic.papers : 'Материалы этого раздела ещё не выпущены'),el('p',{},topic.papers ? 'Во вкладке «Источники» — проверенная библиография. Концептуальный обзор ещё готовится.' : 'Сейчас показаны только тема и вопросы. Готовых научных выводов пока нет.')),el('span',{class:'small-arrow','aria-hidden':'true'},'↗')));
  const main = el('div',{},card,topic.bibliography_coverage?.count ? coveragePanel(topic) : null,el('div',{class:'topic-tags'},topic.subtopics.map(name=>el('span',{},name))));
  return el('div',{class:'content-grid'},main,methodNote());
}

function citation(claimId, claims, papers) {
  const claim = claims.get(claimId), number = claim ? papers.findIndex(p=>p.id === claim.paper_id)+1 : 0;
  if (!claim || !number) return el('span',{class:'review-notice'},'Ссылка требует проверки');
  return el('button',{class:'citation',title:papers[number-1].title,'aria-label':'Показать доказательство: '+papers[number-1].title,onClick:()=>{
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

function topicTree(topic) {
  const nodes = topic.tree || topic.subtopics.map(label=>({label}));
  function branch(node) {
    if (node.children) return el('li',{},el('details',{open:true},el('summary',{},node.label),el('ul',{},node.children.map(branch))));
    const ready = topic.has_review && node.section && state.detail.review.synthesis.sections.some(s=>s.key===node.section);
    return el('li',{},ready ? el('button',{class:'tree-link',onClick:()=>{
      setReadingLevel('detail'); document.getElementById('section-'+node.section)?.scrollIntoView({behavior:'smooth',block:'start'});
    }},node.label+' ↗') : el('span',{},node.label,el('small',{},' · обзор не опубликован')));
  }
  return el('details',{class:'topic-tree',open:topic.has_review ? null : ''},el('summary',{},'Дерево подтем · '+display[topic.id].short),
    el('p',{},'Активные ссылки ведут к разделам опубликованного обзора. Остальные ветви показывают структуру направления.'),el('ul',{},nodes.map(branch)));
}

function comparisonTable(review, claims, papers) {
  return el('div',{class:'comparison-scroll',tabindex:'0','aria-label':'Сравнение исследований; таблицу можно прокручивать'},
    el('table',{class:'comparison-table'},el('caption',{},'Механизмы, гарантии и условия; результаты авторов, без нашего воспроизведения'),
      el('thead',{},el('tr',{},['Работа','Механизм','Гарантия','Условия'].map(title=>el('th',{scope:'col'},title)))),
      el('tbody',{},review.comparison.map(row=>el('tr',{},el('th',{scope:'row'},papers.find(p=>p.id===row.paper_id).title,row.claim_ids.map(id=>citation(id,claims,papers))),
        ...['method','guarantee','setup'].map(key=>el('td',{},row[key])))))));
}

function scientificItem(item, claims, papers) {
  return el('p',{},item.kind === 'editorial' ? el('span',{class:'editorial'},'Комментарий к обзору · ') : null,
    item.text, item.claim_ids.map(id=>citation(id,claims,papers)));
}

function renderReview(review, topic) {
  const papers = review.papers, claims = new Map(review.cards.flatMap(card=>card.claims.map(claim=>[claim.id,claim])));
  const layered = review.synthesis.sections.some(section=>section.key==='brief');
  const article = el('article',{class:'review-article'+(layered?' brief-mode':'')},el('h2',{},review.synthesis.title),el('div',{class:'review-notice'},'Разбор выбранных источников. Независимая экспертная рецензия и собственное воспроизведение экспериментов не выполнялись.'));
  if (layered) article.append(el('div',{class:'reading-levels','aria-label':'Глубина чтения'},
    ...[['brief','За минуту'],['detail','Подробный разбор и таблица']].map(([level,label])=>el('button',{'data-reading-level':level,'aria-pressed':String(level==='brief'),onClick:()=>setReadingLevel(level)},label))));
  for (const section of review.synthesis.sections) article.append(el('section',{class:'review-section','data-key':section.key,id:'section-'+section.key},el('h2',{},section.title),section.items.map(item=>scientificItem(item,claims,papers)),
    section.key==='comparison' && review.comparison ? comparisonTable(review,claims,papers) : null));
  const evidencePanel = el('section',{class:'evidence-panel'},el('h2',{},'Доказательства и цитаты'));
  for (const card of review.cards) {
    const paper = papers.find(p=>p.id === card.paper_id);
    const items = card.claims.map(claim=>el('li',{class:'claim',id:'claim-'+claim.id,tabindex:'-1'},el('small',{},claim.kind+' · '+card.coverage),el('p',{},claim.text),
      claim.conditions.length ? el('p',{},'Условия: '+claim.conditions.join('; ')) : null,
      claim.limitations.length ? el('p',{},'Ограничения и оговорки: '+claim.limitations.join('; ')) : null,
      claim.evidence.map(evidence=>el('div',{},el('blockquote',{},evidence.quote),el('small',{},safeURL(evidence.url) ? el('a',{href:safeURL(evidence.url),target:'_blank',rel:'noreferrer'},evidence.locator+' · открыть первоисточник ↗') : 'Локатор: '+evidence.segment_id)))));
    evidencePanel.append(el('details',{},el('summary',{},paper?.title || card.paper_id),el('ul',{class:'claims-list'},items)));
  }
  article.append(evidencePanel);
  if (review.quality.warnings?.length) article.append(el('h2',{},'Ограничения обзора'),...review.quality.warnings.map(w=>el('p',{},w)));
  const sidebar = el('aside',{class:'review-sidebar'},el('div',{class:'section-nav'},el('div',{class:'panel-kicker'},`${papers.length} СТАТЕЙ · ${review.synthesis.concepts.length} ПОНЯТИЙ`),review.synthesis.sections.map(section=>el('a',{href:'#section-'+section.key,onClick:event=>{event.preventDefault();setReadingLevel('detail');document.getElementById('section-'+section.key)?.scrollIntoView({behavior:'smooth'});}},section.title)),el('div',{class:'panel-kicker'},'КАРТОЧКИ ПОНЯТИЙ'),el('div',{class:'concept-list'},review.synthesis.concepts.map(concept=>el('details',{},el('summary',{},concept.name),scientificItem(concept.definition,claims,papers),concept.statements.map(item=>scientificItem(item,claims,papers)))))));
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
  const order = el('select',{'aria-label':'Порядок источников'},el('option',{value:'newest'},'Сначала новые'),el('option',{value:'oldest'},'Сначала ранние'));
  const summary = el('span',{class:'source-summary'}), more = el('button',{class:'button secondary',type:'button'},'Показать ещё');
  const labels = {core:'Основной корпус',external:'Внешняя работа',reading_list:'Кандидат · метаданные проверены'};
  let visible = 25;
  function render() {
    const query = search.value.toLocaleLowerCase();
    const shown = papers.filter(p=>(p.title+' '+(p.authors||[]).join(' ')).toLocaleLowerCase().includes(query)
      && (!year.value || String(p.year)===year.value) && (!group.value || (p.discovery_groups||[]).includes(group.value)))
      .sort((a,b)=>order.value==='oldest' ? (a.published_at||String(a.year)).localeCompare(b.published_at||String(b.year)) : (b.published_at||String(b.year)).localeCompare(a.published_at||String(a.year)));
    summary.textContent = `Показано ${Math.min(visible,shown.length)} из ${shown.length} · всего в разделе ${papers.length}`;
    list.replaceChildren(...shown.slice(0,visible).map(paper=>{
      const url = safeURL(paper.url), title = url ? el('a',{href:url,target:'_blank',rel:'noreferrer'},paper.title+' ↗') : paper.title;
      return el('li',{class:'source-card'},el('div',{class:'source-meta'},paper.published_at ? date(paper.published_at) : paper.year||'Год не указан',el('span',{class:'source-status'},labels[paper.decision]||paper.decision)),el('h3',{},title),el('p',{},(paper.authors||[]).join(', ')),paper.arxiv_id ? el('p',{},'arXiv: '+paper.arxiv_id+(paper.updated_at ? ' · версия '+date(paper.updated_at) : '')) : null,
        paper.discovery_groups?.length ? el('p',{},'Найдено: '+paper.discovery_groups.map(key=>discoveryLabels[key]||key).join(' · ')) : null,
        paper.added_at ? el('p',{},'Добавлено в каталог: '+date(paper.added_at)) : null,
        paper.available_arxiv_id ? el('p',{class:'review-notice'},'Обзор использует '+paper.arxiv_id+'. Доступна более новая версия: ',el('a',{href:'https://arxiv.org/abs/'+paper.available_arxiv_id,target:'_blank',rel:'noreferrer'},paper.available_arxiv_id+' ↗')) : null);
    }));
    if (!shown.length) list.append(el('li',{class:'empty-panel'},el('p',{},'Нет совпадений с выбранными фильтрами.')));
    more.hidden = visible >= shown.length;
  }
  for (const control of [search,year,group,order]) control.addEventListener(control===search?'input':'change',()=>{visible=25;render();});
  more.addEventListener('click',()=>{visible+=25;render();});
  if (papers.some(p=>p.decision==='reading_list')) wrap.append(coveragePanel(topic),el('p',{class:'review-notice'},(topic.has_review ? topic.review_papers+' работ разобраны в опубликованном обзоре; остальные записи — дополнительные кандидаты. ' : 'Поисковый корпус для будущего обзора. ')+'Включение в выдачу не подтверждает научную значимость или качество работы. Дата поиска: '+date(topic.bibliography_checked_at)));
  wrap.append(el('div',{class:'source-filters'},search,year,group,order),el('div',{class:'source-toolbar'},summary,el('a',{href:'./exports/reading-lists/'+topic.id+'/references.bib',download:'references.bib'},'Скачать BibTeX ↓')),list,more);
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
  $('#view-caption').textContent = state.view === 'sources' ? 'СОХРАНЁННЫЙ КОРПУС ЛИТЕРАТУРЫ' : state.view === 'history' ? 'ВЕРСИИ СОХРАНЯЮТСЯ' : 'ОТ ПОНЯТИЙ К ПЕРВОИСТОЧНИКАМ';
  if (!state.detail) {panel.replaceChildren(el('div',{class:'loading'},'Загружаем данные темы…'));return;}
  panel.replaceChildren(...(state.view === 'overview' ? [topicTree(topic)] : []),state.view === 'sources' ? renderSources(topic) : state.view === 'history' ? renderHistory(topic) : state.detail.review ? renderReview(state.detail.review,topic) : renderEmptyOverview(topic));
}

async function selectTopic(id) {
  if (!state.data.topics.some(topic=>topic.id===id)) id=state.data.topics[0].id;
  const request = ++state.request; state.topic=id;state.detail=null;state.view='overview';
  renderNav();renderHeader();renderPanel();
  try {const detail = await readData('./data/topics/'+id+'.json');if (request!==state.request) return;state.detail=detail;renderPanel();}
  catch(error) {if (request===state.request) $('#view-panel').replaceChildren(emptyPanel('!','Не удалось прочитать тему.',error.message));}
}

document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.close).close()));
$('#about-button').addEventListener('click',()=>$('#method-dialog').showModal());
$('#method-button').addEventListener('click',()=>$('#method-dialog').showModal());
$('#read-overview').addEventListener('click',()=>{$('#tab-overview').click();$('#view-panel').scrollIntoView({behavior:'smooth'});});
$('#read-sources').addEventListener('click',()=>{$('#tab-sources').click();$('#view-panel').scrollIntoView({behavior:'smooth'});});
window.addEventListener('hashchange',()=>{const id=location.hash.slice(1);if(state.data && Object.hasOwn(display,id)) selectTopic(id);});
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
    await selectTopic(location.hash.slice(1)||state.data.topics.find(topic=>topic.has_review)?.id||'safety');
  } catch(error) {
    $('#view-panel').replaceChildren(el('section',{class:'offline-error'},el('h2',{},'Библиотека временно недоступна.'),el('p',{},'Попробуйте обновить страницу. Возможно, статические файлы ещё не размещены полностью.'),el('p',{},error.message)));
  }
}
boot();
