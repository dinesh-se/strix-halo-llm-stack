# Host tuning (Strix Halo / Ryzen AI Max+ 395)

## BIOS: UMA / VRAM carveout

Set the BIOS-level unified-memory-architecture (UMA) VRAM carveout to reserve
most of system RAM for the GPU. On a 128 GiB box, a 96 GiB carveout leaves
~30 GiB for the OS + host-side processes. This is the real memory ceiling for
model sizing — see "VRAM vs GTT" below for why the numbers below the carveout
line don't actually rescue you.

Check the live carveout:

```sh
cat /sys/class/drm/card*/device/mem_info_vram_total   # bytes, should match your BIOS setting
```

## Kernel command line

Add to `GRUB_CMDLINE_LINUX_DEFAULT` in `/etc/default/grub`, then
`update-grub` and reboot:

```
amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856
```

- `amd_iommu=off` — measured +5–12% token-generation throughput on this class
  of hardware. IOMMU isolation isn't needed for a single-user inference box.
- `amdgpu.gttsize=126976` (MiB) + `ttm.pages_limit=32505856` — raises the GTT
  (GPU-accessible system-RAM) ceiling. Community-standard Strix Halo tuning.

## VRAM vs GTT — the ceiling you actually have

These kernel params raise the **GTT ceiling**, not VRAM. `sysfs` still shows
`vram_total` pinned to your BIOS carveout; `gtt_total` becomes much larger
(matching `gttsize` above). GTT is system RAM the GPU can borrow — but after
the carveout, the OS only has the *remaining* physical RAM to back it. On a
128 GiB box with a 96 GiB carveout, that's ~30 GiB backing a GTT pool that
nominally claims 124 GiB. Most of the GTT number is unbacked.

**Practical rule: size your models against the VRAM carveout, not the GTT
figure.** Anything that spills past the carveout lands in the small, heavily
contended OS RAM pool — this is a known freeze/thrash mode on Strix Halo, not
a graceful degradation. `RADV_PERFTEST=nogttspill` (set as a container env
var, see `docker-compose.yml`) is a Mesa RADV perf fix that also helps avoid
this spill path.

To size a candidate GGUF against your carveout, use
`tools/gguf-vram-estimator.py <path-to-gguf> -c <context-size>` — it reads the
GGUF header directly (no need to load the model) and reports weight size + KV
cache size at whatever context lengths you pass.

## Host-RAM OOM watch-item

Even with the GPU-side carveout sized correctly, `llama-server`'s host-side
process (not GPU memory) can still OOM in that tight ~30 GiB OS partition
under sustained load — anonymous RSS has been observed climbing well above
its cold-start baseline during long streaming sessions. If you run multiple
co-resident model instances, watch host RSS per `llama-server` process, not
just GPU VRAM:

```sh
ps -eo pid,rss,args | grep llama-server | grep -v grep
```

A kernel-level OOM kill of `llama-server` shows up in `journalctl -k` as
`Out of memory: Killed process ... (llama-server)`.

**Root cause, once it was finally isolated: `--cache-ram`.** llama-server keeps
a host-RAM prompt/idle-slot cache that defaults to **8192 MiB per model** and
is not reliably capacity-enforced on Linux (memory overcommit —
ggml-org/llama.cpp#22629). Three co-resident models each growing toward that
ceiling will exhaust a ~30 GiB OS partition; once swap goes too, the OOM
killer starts taking desktop applications (editor, browser) as collateral,
which makes it look like a desktop problem rather than an inference one. Set
`-cram 0` on every model (see `config/llama-swap.yaml`). Verify it applied —
startup logs print `--cache-idle-slots requires --cache-ram, disabling`.

Two earlier theories that looked convincing and were both wrong, recorded so
you don't spend time on them: (1) a background workload retry-looping against
the model — real, and worth fixing, but not what exhausted RAM; (2) `--no-mmap`
on reloading on-demand models — a genuine contributor, but the kills continued
after removing it. The tell for the real cause is that anon-RSS climbs
monotonically *during* steady streaming rather than spiking at load time.

## `--no-mmap` — use deliberately, not by default

`--no-mmap` forces weights into anonymous (non-evictable) RAM instead of
leaving them file-backed via mmap. This trades a one-time faster cold-load
for weights that can no longer be paged out under memory pressure — which
matters a lot on a host with a tight OS-RAM partition (see above).

Recommended: only set `--no-mmap` on a model you intend to keep **always
resident** (so the anon-RAM cost is a one-time load-time event, not a
recurring one on every on-demand reload). Leave it off for on-demand models
that load and unload repeatedly.

## GPU compute-ring timeouts (mid-stream death that looks like an OOM)

Under heavy prefill on Vulkan, the kernel can time out a compute ring:

```
amdgpu 0000:c6:00.0: ring comp_1.3.0 timeout, signaled seq=..., emitted seq=...
amdgpu 0000:c6:00.0:  Process llama-server pid ...
amdgpu 0000:c6:00.0: Ring comp_1.3.0 reset succeeded
amdgpu 0000:c6:00.0: [drm] device wedged, but recovered through reset
```

The reset "succeeds" at the kernel level, but the process's Vulkan context
does not always survive it. When it doesn't, llama.cpp dies of **SIGABRT**
(a lost-device error, not a segfault and not an OOM kill). What the client
sees is a request that hangs for minutes and then returns an empty or
truncated body; what llama-swap logs is only:

```
httputil: ReverseProxy read error during body copy: unexpected EOF
[WARN] group: running <model> exited: [<model>] upstream exited unexpectedly
```

That message is identical to what an OOM kill produces, which is exactly why
this is easy to misdiagnose. Distinguish them:

```sh
journalctl -k | grep -E "ring .* timeout|Out of memory: Killed"
```

A ring timeout with no OOM line means the GPU hung, not that you ran out of
RAM — do not go re-tune your memory budget. To confirm the abort signal, the
core dump's ELF notes carry it (`NT_PRSTATUS` → `signal=6` is SIGABRT;
an OOM kill leaves no core at all, since SIGKILL doesn't dump).

Mitigation: lower `-ub` (physical batch). `-ub` sets how much work one GPU
dispatch does, and the amdgpu compute-ring timeout is per-dispatch (10s by
default), so halving `-ub` shortens the longest dispatch. Keep `-b` (logical
batch) large so prefill batching upstream of the GPU is unaffected. If
timeouts persist at a smaller `-ub`, the long dispatch is coming from
somewhere else (speculative-decode graphs are a candidate) and raising
`amdgpu.lockup_timeout` is the blunter alternative.

Note this is **not** necessarily a kernel regression — verify before blaming a
recent upgrade. Here the same timeouts appeared across two different kernel
releases, and were mistakenly pinned on the newer one until per-boot counts
were actually compared.

## Rotate the per-model stderr logs

The stderr redirects in `config/llama-swap.yaml` use `2>>` (append), so they
grow without bound. Rotate them (needs root):

```sh
sudo tee /etc/logrotate.d/llama-swap >/dev/null <<'EOF'
/home/YOU/llama-stack/logs/*.stderr.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
EOF
```

`copytruncate` matters: the logs are held open by long-lived `llama-server`
processes that will not reopen a rotated-away file descriptor, so a plain
`create` rotation would silently send subsequent output nowhere.

## Verify the GPU is actually being used

llama.cpp's Vulkan GPU detection across builds has not been monotonic —
some builds silently fall back to CPU (roughly 4x slower) with no error,
just a much lower token rate. Before pinning any new build, always check:

```sh
docker run --rm --device /dev/dri --group-add video \
  ghcr.io/mostlygeek/llama-swap:vulkan /app/llama-server --list-devices
```

Confirm your GPU (e.g. `RADV GFX1151` for Strix Halo) appears in the device
list before trusting the image for real workloads.
