import { K8s, registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { Alert, Box, Chip, FormControl, InputLabel, MenuItem, Select, Stack, Typography } from '@mui/material';
import React from 'react';

type FabricStatus = {
  node: string;
  observedAt?: string;
  datapath?: {mode?: string; tunnelInterfaces?: string[]};
  frr?: {state?: string; bgp?: unknown; bfd?: unknown};
  ecmpRoutes?: unknown[];
  peerRoutes?: unknown[];
  pathRankings?: Record<string, unknown>;
};

function parseStatus(item: any): FabricStatus | null {
  try { return JSON.parse(item.jsonData?.data?.['status.json'] || item.data?.['status.json']); } catch { return null; }
}

function bgpPeers(value: any): string {
  let up = 0;
  let total = 0;
  function visit(current: any) {
    if (!current || typeof current !== 'object') return;
    for (const [key, child] of Object.entries(current)) {
      if (key === 'peers' && child && typeof child === 'object') {
        for (const peer of Object.values(child) as any[]) {
          total += 1;
          if (String(peer?.state || peer?.peerState).toLowerCase() === 'established') up += 1;
        }
      } else visit(child);
    }
  }
  visit(value);
  return `${up}/${total}`;
}

function Dashboard() {
  const [maps, error] = K8s.ResourceClasses.ConfigMap.useList({namespace: 'kube-system'} as any);
  const statuses = (maps || [])
    .filter((item: any) => item.metadata?.labels?.['networking.re8ch.com/node-status'] === 'true')
    .map(parseStatus).filter(Boolean).sort((a: FabricStatus, b: FabricStatus) => a.node.localeCompare(b.node)) as FabricStatus[];
  const stale = statuses.filter(item => Date.now() - Date.parse(item.observedAt || '') > 90000).length;
  const [selectedNode, setSelectedNode] = React.useState('');
  const effectiveNode = statuses.some(item => item.node === selectedNode) ? selectedNode : statuses[0]?.node || '';
  const selected = statuses.find(item => item.node === effectiveNode);
  const columns = [
    {header: 'Node', accessorKey: 'node'},
    {header: 'Datapath', accessorFn: (item: FabricStatus) => <Chip size="small" color={item.datapath?.mode === 'native' ? 'success' : 'warning'} label={item.datapath?.mode || 'unknown'} />},
    {header: 'Tunnel interface', accessorFn: (item: FabricStatus) => item.datapath?.tunnelInterfaces?.join(', ') || '—'},
    {header: 'FRR', accessorFn: (item: FabricStatus) => <StatusLabel status={item.frr?.state === 'active' ? 'success' : 'error'}>{item.frr?.state || 'unknown'}</StatusLabel>},
    {header: 'BGP established', accessorFn: (item: FabricStatus) => bgpPeers(item.frr?.bgp)},
    {header: 'ECMP routes', accessorFn: (item: FabricStatus) => item.ecmpRoutes?.length || 0},
    {header: 'Known peers', accessorFn: (item: FabricStatus) => item.peerRoutes?.length || 0},
    {header: 'Observed', accessorFn: (item: FabricStatus) => item.observedAt ? new Date(item.observedAt).toLocaleString() : 'unknown'}
  ];
  const ecmpRows = (selected?.ecmpRoutes || []).map((route: any) => ({
    destination: route.dst || 'default', protocol: route.protocol || '—', metric: route.metric ?? '—',
    nextHops: (route.nexthops || []).map((hop: any) => `${hop.gateway || 'on-link'} · ${hop.dev || '?'} · weight ${hop.weight || 1}`)
  }));
  const decisionRows = Object.entries(selected?.pathRankings || {}).flatMap(([profile, paths]: any) =>
    (paths || []).map((path: any, index: number) => ({profile, rank: index + 1, peer: path.peer,
      pathType: path.pathType || '—', score: path.score, quota: path.quotaPressure?.tier || 'unknown',
      price: path.priceStatus || 'unknown'})));
  const peerRows = (selected?.peerRoutes || []).map((peer: any) => ({name: peer.name, role: peer.role || '—',
    class: peer.class || '—', internalIP: peer.internalIP || '—', acceleratedIP: peer.acceleratedIP || '—', podCIDR: peer.podCIDR || '—'}));
  return <Box sx={{p: 2}}>
    <Typography variant="h4">Advanced Fabric</Typography>
    <Typography color="text.secondary">各节点实际 Native/VXLAN、FRR/BGP/BFD 与内核 ECMP 视图。</Typography>
    {error && <Alert severity="error">无法读取状态 ConfigMap：{String(error)}</Alert>}
    {stale > 0 && <Alert severity="warning">{stale} 个节点状态超过 90 秒未更新</Alert>}
    <SectionBox title={`Node network status (${statuses.length})`}><Table data={statuses} columns={columns as any}/></SectionBox>
    <Stack direction={{xs: 'column', md: 'row'}} spacing={2} sx={{my: 2}} alignItems="center">
      <FormControl size="small" sx={{minWidth: 280}}>
        <InputLabel id="af-node-label">Inspect node</InputLabel>
        <Select labelId="af-node-label" label="Inspect node" value={effectiveNode} onChange={event => setSelectedNode(event.target.value)}>
          {statuses.map(item => <MenuItem key={item.node} value={item.node}>{item.node}</MenuItem>)}
        </Select>
      </FormControl>
      {selected && <Stack direction="row" spacing={1} flexWrap="wrap">
        <Chip label={`Kernel ECMP sets ${ecmpRows.length}`} color={ecmpRows.length ? 'success' : 'default'}/>
        <Chip label={`Candidate decisions ${decisionRows.length}`} color={decisionRows.length ? 'primary' : 'default'}/>
        <Chip label={`Known peers ${peerRows.length}`}/>
      </Stack>}
    </Stack>
    <SectionBox title={`${effectiveNode || 'Node'}: kernel ECMP routes`}>
      <Table data={ecmpRows} columns={[
        {header: 'Destination', accessorKey: 'destination'}, {header: 'Protocol', accessorKey: 'protocol'},
        {header: 'Metric', accessorKey: 'metric'},
        {header: 'Next hops / weight', accessorFn: (row: any) => <Stack direction="row" gap={0.5} flexWrap="wrap">{row.nextHops.map((hop: string) => <Chip key={hop} size="small" label={hop}/>)}</Stack>}
      ] as any}/>
    </SectionBox>
    <SectionBox title={`${effectiveNode || 'Node'}: path decisions`}>
      <Table data={decisionRows} columns={[
        {header: 'Profile', accessorKey: 'profile'}, {header: 'Rank', accessorKey: 'rank'},
        {header: 'Peer', accessorKey: 'peer'}, {header: 'Path type', accessorKey: 'pathType'},
        {header: 'Score', accessorKey: 'score'}, {header: 'Quota', accessorKey: 'quota'}, {header: 'Price', accessorKey: 'price'}
      ] as any}/>
    </SectionBox>
    <SectionBox title={`${effectiveNode || 'Node'}: peer inventory`}>
      <Table data={peerRows} columns={[
        {header: 'Peer', accessorKey: 'name'}, {header: 'Class', accessorKey: 'class'}, {header: 'Role', accessorKey: 'role'},
        {header: 'Internal IP', accessorKey: 'internalIP'}, {header: 'Accelerated IP', accessorKey: 'acceleratedIP'}, {header: 'PodCIDR', accessorKey: 'podCIDR'}
      ] as any}/>
    </SectionBox>
  </Box>;
}

registerSidebarEntry({name: 'advanced-fabric', url: '/advanced-fabric', icon: 'mdi:router-network', parent: '', label: 'Advanced Fabric'});
registerRoute({path: '/advanced-fabric', sidebar: 'advanced-fabric', name: 'Advanced Fabric', component: () => <Dashboard/>});
