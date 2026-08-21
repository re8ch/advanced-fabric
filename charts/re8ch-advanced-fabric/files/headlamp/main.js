(function(root,factory){if(typeof exports==='object'&&typeof module!=='undefined'){factory(require('react/jsx-runtime'),require('@kinvolk/headlamp-plugin/lib'),require('@kinvolk/headlamp-plugin/lib/components/common'),require('@mui/material'));}else if(typeof define==='function'&&define.amd){define(['react/jsx-runtime','@kinvolk/headlamp-plugin/lib','@kinvolk/headlamp-plugin/lib/components/common','@mui/material'],factory);}else{root=typeof globalThis!=='undefined'?globalThis:root||self;factory(root.pluginLib.ReactJSX,root.pluginLib,root.pluginLib.CommonComponents,root.pluginLib.MuiMaterial);}})(this,function(jsx,plugin,common,mui){'use strict';
  const STATUS_LABEL='networking.re8ch.com/node-status';
  function parse(cm){try{return JSON.parse((cm.jsonData.data||{})['status.json']||'{}');}catch(_){return {};}}
  function peerCount(bgp){let up=0,total=0;function walk(v){if(!v||typeof v!=='object')return;for(const [k,x] of Object.entries(v)){if(k==='peers'&&x&&typeof x==='object'){for(const p of Object.values(x)){total++;if(p&&['Established','established'].includes(p.state||p.peerState))up++;}}else walk(x);}}walk(bgp);return `${up}/${total}`;}
  function age(iso){const ms=Date.now()-Date.parse(iso||0);if(!Number.isFinite(ms))return 'unknown';return ms<90000?`${Math.max(0,Math.floor(ms/1000))}s`:`${Math.floor(ms/60000)}m`;}
  function View(){const [listed,error]=plugin.K8s.ResourceClasses.ConfigMap.useList({namespace:'kube-system'});const items=(listed||[]).filter(x=>(((x.metadata||{}).labels)||{})[STATUS_LABEL]==='true').map(parse).sort((a,b)=>(a.node||'').localeCompare(b.node||''));
    const columns=[
      {header:'Node',accessorKey:'node'},
      {header:'Datapath',accessorFn:r=>r.datapath&&r.datapath.mode,Cell:({getValue})=>jsx.jsx(mui.Chip,{size:'small',color:getValue()==='native'?'success':'warning',label:getValue()||'unknown'})},
      {header:'Tunnel interface',accessorFn:r=>((r.datapath&&r.datapath.tunnelInterfaces)||[]).join(', ')||'—'},
      {header:'FRR',accessorFn:r=>r.frr&&r.frr.state,Cell:({getValue})=>jsx.jsx(mui.Chip,{size:'small',color:getValue()==='active'?'success':'error',label:getValue()||'unknown'})},
      {header:'BGP established',accessorFn:r=>peerCount(r.frr&&r.frr.bgp)},
      {header:'ECMP routes',accessorFn:r=>(r.ecmpRoutes||[]).length},
      {header:'Known peers',accessorFn:r=>(r.peerRoutes||[]).length},
      {header:'Observed',accessorFn:r=>age(r.observedAt)}
    ];
    return jsx.jsxs(mui.Box,{sx:{p:2},children:[jsx.jsx(mui.Typography,{variant:'h4',children:'Advanced Fabric'}),jsx.jsx(mui.Alert,{severity:'info',sx:{my:2},children:'Live host evidence. A failed node is repaired forward; it does not trigger a global fabric rollback.'}),error&&jsx.jsxs(mui.Alert,{severity:'error',sx:{mb:2},children:['Unable to read status ConfigMaps: ',String(error)]}),jsx.jsx(common.SectionBox,{title:`Node network status (${items.length})`,children:jsx.jsx(common.Table,{data:items,columns})}),items.map(r=>jsx.jsxs(mui.Accordion,{children:[jsx.jsx(mui.AccordionSummary,{children:jsx.jsx(mui.Typography,{children:`${r.node}: per-node ECMP view`})}),jsx.jsx(mui.AccordionDetails,{children:jsx.jsx('pre',{style:{whiteSpace:'pre-wrap',overflow:'auto'},children:JSON.stringify({ecmpRoutes:r.ecmpRoutes||[],peerRoutes:r.peerRoutes||[],pathRankings:r.pathRankings||{}},null,2)})})]},r.node))]});}
  plugin.registerSidebarEntry({parent:'cluster',name:'advanced-fabric',label:'Advanced Fabric',url:'/advanced-fabric',icon:'mdi:router-network'});
  plugin.registerRoute({path:'/advanced-fabric',sidebar:'advanced-fabric',name:'advanced-fabric',exact:true,component:View});
});
