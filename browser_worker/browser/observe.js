/* Fixed, operator-owned DOM reader. Model text is never evaluated as code. */
({attribute, maxElements, maxText, config}) => {
  const visible = el => el instanceof Element && el.checkVisibility({checkOpacity:true, checkVisibilityCSS:true})
    && !el.closest('[hidden],[aria-hidden="true"]');
  const sensitive = /password|passwd|secret|token|api.?key|cookie|authorization/i;
  const safe = el => {
    if(el.closest('script,style,noscript,nav,footer,[contenteditable="true"]')) return false;
    for(let node = el; node; node=node.parentElement) {
      if([node.type, node.name, node.id, node.getAttribute('autocomplete')].some(x=>sensitive.test(x || ''))) return false;
    }
    return true;
  };
  const trim = (s, n=400) => (s || '').replace(/\s+/g, ' ').trim().slice(0,n);
  const cleanText = el => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const parts = [];
    while(walker.nextNode()) {
      const p = walker.currentNode.parentElement;
      if (p && visible(p) && safe(p)) parts.push(walker.currentNode.textContent);
    }
    return trim(parts.join(' '), maxText);
  };
  const selector = 'input,textarea,select,button,a[href],[role],form,label,article,section,h1,h2,h3,p,span,div,li,ul,ol,table,tr,th,td,dt,dd';
  const all = [...document.querySelectorAll(selector)].filter(e => visible(e) && safe(e));
  const nodes = all.slice(0,maxElements);
  const ids = new Map(nodes.map((el,i) => [el, `e${i+1}`]));
  document.querySelectorAll(`[${attribute}]`).forEach(el=>el.removeAttribute(attribute));
  for (const [el,id] of ids) el.setAttribute(attribute,id);
  const implicit = el => ({BUTTON:'button',A:'link',SELECT:'combobox',TEXTAREA:'textbox',ARTICLE:'article',FORM:'form',TABLE:'table',TR:'row',TD:'cell',TH:'columnheader',LI:'listitem',UL:'list',OL:'list'})[el.tagName]
    || (el.tagName === 'INPUT' ? ({checkbox:'checkbox',radio:'radio',submit:'button',button:'button'})[el.type] || 'textbox' : 'generic');
  const name = el => trim(el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby') || '').split(' ').map(id=>document.getElementById(id)?.textContent || '').join(' ')
    || [...(el.labels || [])].map(l => l.textContent).join(' ') || el.getAttribute('placeholder') || (['button','link','columnheader'].includes(implicit(el)) ? el.textContent : '') || el.getAttribute('title'));
  const elements = nodes.map(el => {
    let parent = el.parentElement;
    while(parent && !ids.has(parent)) parent = parent.parentElement;
    const attrs = {};
    for(const key of ['name','placeholder','data-testid','aria-expanded','aria-selected','aria-busy']) {
      if(el.hasAttribute(key)) attrs[key] = trim(el.getAttribute(key));
    }
    const rules = config.controls.filter(rule => el.matches(rule.selector));
    const bindings = [...new Set(rules.map(r=>r.parameter).filter(Boolean))];
    if(bindings.length === 1) attrs.bound_parameter = bindings[0];
    return {ref:ids.get(el),tag:el.tagName.toLowerCase(),role:el.getAttribute('role') || implicit(el),name:name(el),
      text:trim(cleanText(el),800),text_truncated:cleanText(el).length > 800,
      value: ['INPUT','SELECT','TEXTAREA'].includes(el.tagName) ? trim(el.value) : null,
      href:el.tagName === 'A' ? el.href : null, input_type:el.tagName === 'INPUT' ? el.type : null,
      disabled:!!el.disabled || el.getAttribute('aria-disabled') === 'true',parent_ref:parent ? ids.get(parent):null,
      attributes:attrs, options:el.tagName === 'SELECT' ? [...el.options].map(o=>({value:o.value,label:trim(o.label)})).slice(0,100) : [],
      permitted_actions:bindings.length > 1 ? [] : [...new Set(rules.flatMap(r=>r.actions))]};
  });
  const matches = selector => [...document.querySelectorAll(selector)].filter(visible);
  const applied = {};
  for(const proof of config.parameter_evidence) {
    const found = matches(proof.selector);
    applied[proof.parameter] = found.length === 1 ? trim(proof.attribute === 'value' ? found[0].value : cleanText(found[0])) : null;
  }
  return {title:trim(document.title), visible_text:cleanText(document.body), elements,
    signals:{results_ready:matches(config.results_selector).length === 1,
      record_refs:matches(config.record_selector).map(e=>ids.get(e)).filter(Boolean),
      record_count:matches(config.record_selector).length,
      empty:matches(config.empty_selector).length === 1,
      loading:matches(config.loading_selector).length > 0,
      error:matches(config.error_selector).length > 0,
      auth_required:matches(config.auth_selector).length > 0,
      canvas:matches('canvas').length > 0, applied_parameters:applied},
    truncated:all.length > maxElements || cleanText(document.body).length >= maxText,
    limitations:document.querySelector('iframe') ? ['Iframe contents are not observed.'] : []};
}
