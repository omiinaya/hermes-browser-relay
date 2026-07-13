(function(){'use strict';let hermesReady=false;let pendingCommands=[];function waitForHermes(callback){if(window.__hermes&&window.__hermes.isLoaded&&window.__hermes.isLoaded()){hermesReady=true;callback();return;}
const poll=setInterval(()=>{if(window.__hermes&&window.__hermes.isLoaded&&window.__hermes.isLoaded()){hermesReady=true;clearInterval(poll);clearTimeout(fallback);callback();}},10);const fallback=setTimeout(()=>{clearInterval(poll);hermesReady=false;callback();},5000);}
chrome.runtime.onMessage.addListener((msg,sender,sendResponse)=>{if(msg.type==='hermes'){if(!hermesReady||!window.__hermes){sendResponse({ok:false,error:'__hermes not ready yet',ready:hermesReady});return true;}
try{const fn=window.__hermes[msg.func];if(msg.func==='__eval'){const result=eval(msg.args[0]);sendResponse({ok:true,result:result});return true;}
if(typeof fn!=='function'){sendResponse({ok:false,error:'__hermes.'+msg.func+' is not a function'});return true;}
const result=fn.apply(window.__hermes,msg.args||[]);if(result&&typeof result.then==='function'){result.then(function(resolved){sendResponse({ok:true,result:resolved});},function(err){sendResponse({ok:false,error:''+(err&&err.message||err)});});return true;}
sendResponse({ok:true,result:result});}catch(e){sendResponse({ok:false,error:e.message,stack:e.stack?e.stack.substring(0,500):''});}
return true;}
if(msg.type==='hermes_ping'){sendResponse({ok:hermesReady&&!!window.__hermes,ready:hermesReady});return true;}
return false;});let lastStateKey='';let pushThrottle=false;function getStateSnapshot(){const h=window.__hermes;if(!h||!h.getStateSnapshot)return null;try{return h.getStateSnapshot();}catch(e){return null;}}
function sendPushEvent(type,extra){const state=getStateSnapshot();if(!state)return;const key=`${state.total}:${state.visible}:${state.readyState}`;if(key===lastStateKey)return;lastStateKey=key;try{chrome.runtime.sendMessage(Object.assign({type:type},extra,{state:state}));}catch(e){}}
const observer=new MutationObserver(()=>{if(pushThrottle)return;pushThrottle=true;setTimeout(()=>{pushThrottle=false;},500);sendPushEvent('page_updated');});function startObserver(){try{observer.observe(document,{childList:true,subtree:true,attributes:true,characterData:false,});}catch(e){}}
waitForHermes(()=>{sendPushEvent('page_loaded',{url:location.href,title:document.title,});startObserver();});})();
