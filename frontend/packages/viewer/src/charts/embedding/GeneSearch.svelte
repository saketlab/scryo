<!-- scryo: Gene search with assay toggle -->
<script lang="ts">
  import { type Coordinator } from "@uwdata/mosaic-core";

  interface Props {
    coordinator: Coordinator;
    onGeneSelect: (column: string) => void;
  }

  let { coordinator, onGeneSelect }: Props = $props();

  let searchQuery = $state("");
  let genes: string[] = $state([]);
  let assays: string[] = $state([]);
  let selectedAssay = $state("RNA");
  let showDropdown = $state(false);
  let pinnedGenes: { name: string; assay: string }[] = $state([]);
  let activeGene: string | null = $state(null);
  let loading = $state(false);
  let highlightIndex = $state(-1);
  let genesLoaded = $state(false);

  // Fetch gene list on mount (run once)
  if (!genesLoaded) {
    fetch("/data/scryo/genes")
      .then((r) => r.json())
      .then((data) => {
        genes = data.genes;
        assays = data.assays;
        genesLoaded = true;
        if (assays.length > 0 && !assays.includes(selectedAssay)) {
          selectedAssay = assays[0];
        }
      });
  }

  // Filter genes as user types (derived, not effect)
  let filteredGenes = $derived.by(() => {
    let q = searchQuery.toLowerCase().trim();
    if (q.length < 1) return [];
    let prefix = genes.filter((g) => g.toLowerCase().startsWith(q));
    let contains = genes.filter((g) => !g.toLowerCase().startsWith(q) && g.toLowerCase().includes(q));
    return [...prefix, ...contains].slice(0, 50);
  });

  async function selectGene(geneName: string) {
    let column = `${geneName}_${selectedAssay}`;
    loading = true;
    showDropdown = false;
    searchQuery = "";

    try {
      // Load gene column into DuckDB dataset table
      let resp = await fetch("/data/scryo/load-column", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ column }),
      });
      let result = await resp.json();
      if (result.error) {
        console.error("[scryo] Failed to load gene:", result.error);
        loading = false;
        return;
      }

      // Pin the gene
      if (!pinnedGenes.find((p) => p.name === geneName && p.assay === selectedAssay)) {
        pinnedGenes = [...pinnedGenes, { name: geneName, assay: selectedAssay }];
      }
      activeGene = geneName;

      // Trigger coloring
      onGeneSelect(column);
    } finally {
      loading = false;
    }
  }

  function onClickPinned(gene: { name: string; assay: string }) {
    selectedAssay = gene.assay;
    activeGene = gene.name;
    selectGene(gene.name);
  }

  function removePinned(gene: { name: string; assay: string }) {
    pinnedGenes = pinnedGenes.filter((p) => !(p.name === gene.name && p.assay === gene.assay));
    if (activeGene === gene.name) {
      activeGene = null;
    }
  }

  function onAssayChange(assay: string) {
    selectedAssay = assay;
    // If there's an active gene, reload with new assay
    if (activeGene) {
      selectGene(activeGene);
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      highlightIndex = Math.min(highlightIndex + 1, filteredGenes.length - 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      highlightIndex = Math.max(highlightIndex - 1, 0);
    } else if (e.key === "Enter" && highlightIndex >= 0 && highlightIndex < filteredGenes.length) {
      e.preventDefault();
      selectGene(filteredGenes[highlightIndex]);
    } else if (e.key === "Escape") {
      showDropdown = false;
      searchQuery = "";
    }
  }

  function handleInput() {
    showDropdown = true;
    highlightIndex = 0;
  }
</script>

<div class="flex flex-col gap-2 text-sm">
  <!-- Assay toggle -->
  {#if assays.length > 1}
    <div class="flex rounded-md overflow-hidden border border-slate-300 dark:border-slate-600">
      {#each assays as assay}
        <button
          class="flex-1 px-3 py-1 text-xs font-medium transition-colors"
          class:bg-blue-500={selectedAssay === assay}
          class:text-white={selectedAssay === assay}
          class:bg-transparent={selectedAssay !== assay}
          class:text-slate-400={selectedAssay !== assay}
          class:hover:bg-slate-100={selectedAssay !== assay}
          class:dark:hover:bg-slate-700={selectedAssay !== assay}
          onclick={() => onAssayChange(assay)}
        >
          {assay}
        </button>
      {/each}
    </div>
  {/if}

  <!-- Search input -->
  <div class="relative">
    <input
      type="text"
      placeholder="Search genes..."
      class="w-full px-3 py-1.5 text-sm rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200 placeholder-slate-400 outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400"
      bind:value={searchQuery}
      onfocus={() => (showDropdown = true)}
      oninput={handleInput}
      onkeydown={handleKeydown}
    />
    {#if loading}
      <div class="absolute right-2 top-1/2 -translate-y-1/2">
        <div class="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin"></div>
      </div>
    {/if}

    <!-- Dropdown results -->
    {#if showDropdown && filteredGenes.length > 0}
      <div
        class="absolute z-50 w-full mt-1 max-h-48 overflow-y-auto rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 shadow-lg"
      >
        {#each filteredGenes as gene, i}
          <button
            class="w-full px-3 py-1.5 text-left text-sm hover:bg-blue-50 dark:hover:bg-slate-700 transition-colors"
            class:bg-blue-50={i === highlightIndex}
            class:dark:bg-slate-700={i === highlightIndex}
            onclick={() => selectGene(gene)}
          >
            <span class="font-medium">{gene}</span>
            <span class="text-slate-400 text-xs ml-1">_{selectedAssay}</span>
          </button>
        {/each}
      </div>
    {/if}
  </div>

  <!-- Pinned genes -->
  {#if pinnedGenes.length > 0}
    <div class="flex flex-col gap-0.5">
      {#each pinnedGenes as gene}
        {@const isActive = activeGene === gene.name && selectedAssay === gene.assay}
        <div
          class="flex items-center justify-between px-2 py-1 rounded-md text-xs cursor-pointer transition-colors {isActive ? 'bg-blue-100 dark:bg-blue-900 text-blue-500' : 'hover:bg-slate-100 dark:hover:bg-slate-700'}"
          onclick={() => onClickPinned(gene)}
        >
          <span>
            <span class="font-medium">{gene.name}</span>
            <span class="text-slate-400">_{gene.assay}</span>
          </span>
          <button
            class="text-slate-400 hover:text-red-400 ml-2 px-1"
            onclick={(e: MouseEvent) => { e.stopPropagation(); removePinned(gene); }}
          >
            ×
          </button>
        </div>
      {/each}
    </div>
  {/if}
</div>
