<!-- scryo: split the embedding into small multiples, one filtered panel per category value -->
<script lang="ts">
  import { EmbeddingViewMosaic } from "@embedding-atlas/component/svelte";
  import { Selection, type Coordinator } from "@uwdata/mosaic-core";
  import * as SQL from "@uwdata/mosaic-sql";

  interface Props {
    coordinator: Coordinator;
    table: string;
    x: string;
    y: string;
    identifier: string;
    rowKey: string | null;
    // category index column + colours from the parent's legend (may be null = single colour)
    category: string | null | undefined;
    categoryColors: string[];
    splitColumn: string;
    values: string[]; // category values to facet by (already capped/ordered)
    config: any;
    width?: number;
    height?: number;
  }

  let {
    coordinator,
    table,
    x,
    y,
    identifier,
    rowKey,
    category,
    categoryColors,
    splitColumn,
    values,
    config,
    width = 600,
    height = 400,
  }: Props = $props();

  const GAP = 6;
  const LABEL_H = 18;

  let cols = $derived(Math.ceil(Math.sqrt(values.length)));
  let rows = $derived(Math.ceil(values.length / cols));
  let cellW = $derived(Math.max(80, Math.floor((width - GAP * (cols - 1)) / cols)));
  let cellH = $derived(Math.max(80, Math.floor((height - GAP * (rows - 1)) / rows) - LABEL_H));

  let sharedViewport = $state.raw<any>(null);

  let filters = $derived.by(() => {
    let byValue = new Map<string, Selection>();
    for (let v of values) {
      let sel = Selection.intersect();
      let source = { split: splitColumn, value: v };
      sel.update({
        source,
        clients: new Set(),
        predicate: SQL.eq(SQL.column(splitColumn), SQL.literal(v)),
        value: v,
      });
      byValue.set(v, sel);
    }
    return byValue;
  });
</script>

<div class="flex flex-wrap" style="gap: {GAP}px;">
  {#each values as value (value)}
    <div class="flex flex-col" style="width: {cellW}px;">
      <div class="truncate text-[11px] font-medium text-slate-600 dark:text-slate-300 px-1" title={value}>
        {value}
      </div>
      <div class="relative rounded overflow-hidden bg-slate-50 dark:bg-slate-900" style="height: {cellH}px;">
        <EmbeddingViewMosaic
          width={cellW}
          height={cellH}
          coordinator={coordinator}
          table={table}
          filter={filters.get(value)}
          identifier={identifier}
          x={x}
          y={y}
          category={category}
          rowKey={rowKey}
          categoryColors={categoryColors}
          config={config}
          viewportState={sharedViewport}
          onViewportState={(v: any) => (sharedViewport = v)}
        />
      </div>
    </div>
  {/each}
</div>
