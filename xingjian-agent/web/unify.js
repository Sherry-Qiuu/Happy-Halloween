let previousSettingsPage='home',previousMemoryPage='home';
const unifyGo=go,unifyRender=render,unifyBind=bind;
go=function(p){if(p==='settings'&&page!=='settings')previousSettingsPage=page;if(p==='memory'&&page!=='memory')previousMemoryPage=page;unifyGo(p)};
render=function(){document.body.classList.toggle('settings-page',page==='settings');unifyRender();if(page==='memory'){$('.memory-page').insertAdjacentHTML('beforeend','<button class="settings-exit" id="exitMemory" aria-label="退出 AI 记忆库" title="退出 AI 记忆库">×</button>');$('#exitMemory').onclick=()=>go(previousMemoryPage==='memory'?'home':previousMemoryPage)}};
bind=function(){unifyBind();if(page==='home'){$('#homeComposer input').placeholder='说说你的想法…'}if(page==='settings'){$('.settings').insertAdjacentHTML('beforeend','<button class="settings-exit" id="exitSettings" aria-label="退出设置" title="退出设置">×</button>');$('#exitSettings').onclick=()=>go(previousSettingsPage==='settings'?'home':previousSettingsPage)}};
