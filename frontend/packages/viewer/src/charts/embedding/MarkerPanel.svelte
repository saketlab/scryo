<!-- scryo: browse precomputed marker genes per cell type -->
<script lang="ts">
  interface Marker {
    gene: string;
    pct_in: number;
    pct_out: number;
  }
  interface Group {
    name: string;
    n: number;
    markers: Marker[];
  }

  interface Props {
    // markers sidecar data, fetched once by the parent (Embedding.svelte)
    groups: Group[];
    annotationColumn: string;
    assay: string;
    activeColumn: string | null | undefined;
    onGeneSelect: (column: string) => void;
    onResetColor: (column: string) => void;
  }

  let { groups, annotationColumn, assay, activeColumn, onGeneSelect, onResetColor }: Props = $props();

  const COLLAPSED = 8;

  let filter = $state("");
  let expanded: string | null = $state(null);
  let showAll = $state(false);
  let hovered: Marker | null = $state(null);

  let activeGene = $derived(
    activeColumn && activeColumn.endsWith(`_${assay}`) ? activeColumn.slice(0, -(assay.length + 1)) : null,
  );

  let shown = $derived.by(() => {
    let q = filter.toLowerCase().trim();
    if (!q) return groups;
    return groups.filter(
      (g) => g.name.toLowerCase().includes(q) || g.markers.some((m) => m.gene.toLowerCase().includes(q)),
    );
  });

  const prefetched = new Set<string>();

  function prefetch(columns: string[]) {
    let wanted = columns.filter((c) => !prefetched.has(c));
    if (wanted.length == 0) return;
    wanted.forEach((c) => prefetched.add(c));
    // warm the columns so a click only pays for the recolour
    fetch("/data/scryo/load-columns", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ columns: wanted }),
    }).catch(() => wanted.forEach((c) => prefetched.delete(c)));
  }

  function toggle(group: Group) {
    showAll = false;
    if (expanded == group.name) {
      expanded = null;
      return;
    }
    expanded = group.name;
    prefetch(group.markers.slice(0, COLLAPSED).map((m) => `${m.gene}_${assay}`));
  }

  function visibleMarkers(group: Group): Marker[] {
    return showAll ? group.markers : group.markers.slice(0, COLLAPSED);
  }

  const pct = (v: number) => `${Math.round(v * 100)}%`;
</script>

<div class="flex flex-col gap-2 text-sm">
  {#if groups.length == 0}
    <div class="text-xs text-slate-500 dark:text-slate-400">No markers for this dataset.</div>
  {:else}
    <input
      type="text"
      placeholder="Filter cell types or genes..."
      class="w-full px-3 py-1.5 text-sm rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200 placeholder-slate-400 outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400"
      bind:value={filter}
    />

    {#if activeGene && annotationColumn}
      <button
        class="w-full px-2 py-1 text-xs rounded border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700"
        onclick={() => onResetColor(annotationColumn)}
      >
        ← colour by {annotationColumn || "cell type"}
      </button>
    {/if}

    <div class="flex flex-col max-h-80 overflow-y-auto -mx-1 px-1">
      {#each shown as group (group.name)}
        <button
          class="flex items-baseline gap-1 w-full py-1 text-left text-xs text-slate-700 dark:text-slate-200 hover:text-blue-600 dark:hover:text-blue-400"
          onclick={() => toggle(group)}
          onmouseenter={() => prefetch(group.markers.slice(0, 3).map((m) => `${m.gene}_${assay}`))}
        >
          <span class="w-3 flex-none text-slate-400">{expanded == group.name ? "▾" : "▸"}</span>
          <span class="flex-1 truncate" title={group.name}>{group.name}</span>
          <span class="flex-none tabular-nums text-slate-400">{group.n.toLocaleString()}</span>
        </button>

        {#if expanded == group.name}
          <div class="flex flex-wrap gap-1 pl-4 pb-1.5">
            {#each visibleMarkers(group) as marker (marker.gene)}
              <button
                class="px-1.5 py-0.5 text-xs rounded border tabular-nums transition-colors {activeGene == marker.gene
                  ? 'border-blue-500 bg-blue-500 text-white'
                  : 'border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:border-blue-400'}"
                onmouseenter={() => {
                  hovered = marker;
                  prefetch([`${marker.gene}_${assay}`]);
                }}
                onmouseleave={() => (hovered = marker == hovered ? null : hovered)}
                onclick={() => onGeneSelect(`${marker.gene}_${assay}`)}
              >
                {marker.gene}
              </button>
            {/each}
            {#if group.markers.length > COLLAPSED}
              <button
                class="px-1.5 py-0.5 text-xs text-slate-500 dark:text-slate-400 hover:text-blue-500"
                onclick={() => (showAll = !showAll)}
              >
                {showAll ? "show fewer" : `⌄ ${group.markers.length - COLLAPSED} more`}
              </button>
            {/if}
          </div>
          <div class="pl-4 pb-1 h-4 text-[11px] text-slate-500 dark:text-slate-400 tabular-nums">
            {#if hovered}
              {hovered.gene} · {pct(hovered.pct_in)} in / {pct(hovered.pct_out)} elsewhere
            {/if}
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</div>
