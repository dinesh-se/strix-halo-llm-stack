# Pre-router baseline — 2026-08-06 17:26:53 UTC

## Memory
```
               total        used        free      shared  buff/cache   available
Mem:             122         103           2           0          17          18
Swap:              7           3           3
GTT used:  98.4 GiB
GTT total: 124.0 GiB
```

## Containers
```
ds4-server	Up 46 minutes
llama-swap	Up About an hour (healthy)
open-webui	Up 6 hours (healthy)
victoriametrics	Up 6 hours
firecrawl-api-1	Up 6 hours
firecrawl-rabbitmq-1	Up 6 hours (healthy)
firecrawl-redis-1	Up 6 hours
firecrawl-nuq-postgres-1	Up 6 hours
firecrawl-playwright-service-1	Up 6 hours
grafana	Up 6 hours
node-exporter	Up 6 hours
searxng	Up 6 hours
```

## Listening ports (ai stack)
```
LISTEN 0      4096         0.0.0.0:9292       0.0.0.0:*   
LISTEN 0      5            0.0.0.0:9610       0.0.0.0:*   
LISTEN 0      4096         0.0.0.0:10097      0.0.0.0:*   
LISTEN 0      4096       127.0.0.1:3001       0.0.0.0:*   
LISTEN 0      4096       127.0.0.1:3000       0.0.0.0:*   
LISTEN 0      4096       127.0.0.1:8428       0.0.0.0:*   
LISTEN 0      4096            [::]:9292          [::]:*   
LISTEN 0      4096            [::]:10097         [::]:*   
```

## User units
```
ds4-server             enabled=disabled   active=active
hindsight-daemon       enabled=enabled    active=inactive
llama-watchdog         enabled=disabled   active=inactive
amdgpu-exporter        enabled=enabled    active=active
hermes-gateway         enabled=enabled    active=active
hermes-dashboard       enabled=enabled    active=active
```

## Endpoints
```
llama-swap /v1/models: ['gemma4-12b', 'qwen3.6-27b', 'qwen3.6-35b']
llama-swap /running:   {"running":[]}
ds4 :10097 /health:    {"status":"ok"}
```
