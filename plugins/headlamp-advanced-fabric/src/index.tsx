import { K8s, registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { Alert, Box, Chip, Typography } from '@mui/material';

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
  return <Box sx={{p: 2}}>
    <Typography variant="h4">Advanced Fabric</Typography>
    <Typography color="text.secondary">各节点实际 Native/VXLAN、FRR/BGP/BFD 与内核 ECMP 视图。</Typography>
    {error && <Alert severity="error">无法读取状态 ConfigMap：{String(error)}</Alert>}
    {stale > 0 && <Alert severity="warning">{stale} 个节点状态超过 90 秒未更新</Alert>}
    <SectionBox title={`Node network status (${statuses.length})`}><Table data={statuses} columns={columns as any}/></SectionBox>
    {statuses.map(item => <SectionBox key={item.node} title={`${item.node}: ECMP / path decision`}>
      <Box component="pre" sx={{whiteSpace: 'pre-wrap', overflow: 'auto'}}>{JSON.stringify({ecmpRoutes: item.ecmpRoutes || [], peerRoutes: item.peerRoutes || [], pathRankings: item.pathRankings || {}}, null, 2)}</Box>
    </SectionBox>)}
  </Box>;
}

registerSidebarEntry({name: 'advanced-fabric', url: '/advanced-fabric', icon: 'mdi:router-network', parent: '', label: 'Advanced Fabric'});
registerRoute({path: '/advanced-fabric', sidebar: 'advanced-fabric', name: 'Advanced Fabric', component: () => <Dashboard/>});
