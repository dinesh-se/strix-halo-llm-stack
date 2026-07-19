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
`Out of memory: Killed process ... (llama-server)` — if you see this, check
what else was competing for host RAM in that window before assuming it's a
model-config problem; on this box it turned out to be an unrelated background
workload retry-looping against the model, not the model itself.

## `--no-mmap` — use deliberately, not by default

`--no-mmap` forces weights into anonymous (non-evictable) RAM instead of
leaving them file-backed via mmap. This trades a one-time faster cold-load
for weights that can no longer be paged out under memory pressure — which
matters a lot on a host with a tight OS-RAM partition (see above).

Recommended: only set `--no-mmap` on a model you intend to keep **always
resident** (so the anon-RAM cost is a one-time load-time event, not a
recurring one on every on-demand reload). Leave it off for on-demand models
that load and unload repeatedly.

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
