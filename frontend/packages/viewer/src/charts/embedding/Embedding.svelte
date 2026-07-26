<!-- Copyright (c) 2025 Apple Inc. Licensed under MIT License. -->
<script module lang="ts">
  import { maxDensityModeCategories, type DataPoint, type Label, type ViewportState } from "@embedding-atlas/component";
  import { type Coordinator } from "@uwdata/mosaic-core";
  import * as SQL from "@uwdata/mosaic-sql";

  import Overlay from "./Overlay.svelte";
  import Tooltip from "./Tooltip.svelte";

  import { type EmbeddingLegend } from "../../utils/database.js";
  import { createCustomComponentClass } from "./custom_components.js";

  async function defaultViewportScale(coordinator: Coordinator, table: string, x: string, y: string): Promise<number> {
    let { stdX, stdY } = (
      await coordinator.query(
        SQL.Query.from(table).select({
          stdX: SQL.sql`STDDEV(${SQL.column(x)})::FLOAT`,
          stdY: SQL.sql`STDDEV(${SQL.column(y)})::FLOAT`,
        }),
      )
    ).get(0);
    let scale = 1.0 / (Math.max(stdX, stdY, 1e-3) * 3);
    return scale;
  }

  const CustomTooltip = createCustomComponentClass(Tooltip);
  const CustomOverlay = createCustomComponentClass(Overlay);
</script>

<script lang="ts">
  import { EmbeddingViewMosaic } from "@embedding-atlas/component/svelte";
  import { cubicOut } from "svelte/easing";

  import Button from "../../widgets/Button.svelte";
  import PopupButton from "../../widgets/PopupButton.svelte";
  import Select from "../../widgets/Select.svelte";
  import SearchableSelect from "../../widgets/SearchableSelect.svelte";
  import Slider from "../../widgets/Slider.svelte";
  import Legend from "./Legend.svelte";

  import { IconSettings } from "../../assets/icons.js";
  import { isolatedWritable } from "../../utils/store.js";
  import type { ChartViewProps, RowID } from "../chart.js";
  import { resolveChartTheme } from "../common/theme.js";
  import {
    makeCategoryColumn,
    CONTINUOUS_SCALES,
    recolorContinuousLegend,
    generatePalette,
  } from "./category_column.js";
  import GeneSearch from "./GeneSearch.svelte";
  import MarkerPanel from "./MarkerPanel.svelte";
  import type { EmbeddingSpec, EmbeddingState } from "./types.js";
  import { interpolateViewport } from "./viewport_animation.js";

  let selectedColorScale = $state("viridis");

  interface ReductionInfo {
    x: string;
    y: string;
  }
  let availableReductions: Record<string, ReductionInfo> = $state({});
  let currentReduction = $state("");

  fetch("/data/scryo/reductions")
    .then((r) => r.json())
    .then((data) => {
      availableReductions = data.reductions ?? {};
      currentReduction = data.default ?? "";
    })
    .catch(() => {});

  function switchReduction(name: string) {
    let reduc = availableReductions[name];
    if (!reduc) return;
    currentReduction = name;
    onSpecChange({ data: { ...spec.data, x: reduc.x, y: reduc.y } });
  }

  // the user's saved choice overrides the server default; keyed by dataset_id
  let colorStorageKey = $state<string | null>(null);
  let colorInitialized = false;

  function persistColor(v: string | undefined) {
    if (colorStorageKey == null) return;
    try {
      localStorage.setItem(colorStorageKey, v ?? "");
    } catch {
      /* localStorage may be unavailable */
    }
  }

  function setColor(v: string | undefined) {
    persistColor(v);
    onSpecChange({ data: { ...spec.data, category: v } });
  }

  fetch("/data/scryo/config")
    .then((r) => r.json())
    .then((cfg) => {
      colorStorageKey = `scryo:${cfg.dataset_id ?? "scryo"}:color`;
      if (colorInitialized) return;
      colorInitialized = true;

      // a saved "" means deliberately cleared, so it still wins over the default
      let raw: string | null = null;
      try {
        raw = localStorage.getItem(colorStorageKey);
      } catch {
        raw = null;
      }
      let initial: string | undefined = raw != null ? (raw === "" ? undefined : raw) : (cfg.default_color ?? undefined);

      let validNames = new Set(context.columns.map((c) => c.name));
      if (initial != null && !validNames.has(initial)) initial = undefined;
      if (initial != null && initial !== categoryColumn) {
        onSpecChange({ data: { ...spec.data, category: initial } });
      }
    })
    .catch(() => {});

  let genePanelTab = $state("genes");

  function handleGeneSelect(column: string) {
    // setting the colour column is what triggers makeCategoryColumn
    onSpecChange({ data: { ...spec.data, category: column } });
  }

  const maxCategories = Math.min(20, maxDensityModeCategories());
  const defaultMinimumDensity = 1 / 16;
  const defaultDownsampleMaxPoints = 4000000;
  const minDownsampleMaxPoints = 50000;

  let {
    context,
    width,
    height,
    spec,
    state: chartState,
    onStateChange,
    onSpecChange,
  }: ChartViewProps<EmbeddingSpec, EmbeddingState> = $props();

  // svelte-ignore state_referenced_locally
  let { colorScheme, columnStyles, searchResult, theme: themeConfig } = context;

  let theme = $derived(resolveChartTheme($colorScheme, $themeConfig));

  // svelte-ignore state_referenced_locally
  let highlightStore = isolatedWritable(context.highlight);

  let categoryColumn = $derived(spec.data.category);

  let categoryLegend: EmbeddingLegend | null = $state.raw(null);
  let totalPointCount: number | null = $state.raw(null);

  // Query total point count for render limit slider
  $effect.pre(() => {
    context.coordinator
      .query(SQL.Query.from(context.table).select({ count: SQL.sql`COUNT(*)::INT` }))
      .then((result: any) => {
        totalPointCount = result.get(0).count;
      });
  });

  let tooltip = $state.raw<DataPoint | null>(null);
  let selection = $state.raw<DataPoint[] | null>(null);
  let overlayProps = $state.raw<{ center: DataPoint | null; points: DataPoint[] } | null>(null);

  // custom overlay; the component's built-in labels prop placed these unreliably
  let showLabels = $state(true);
  let clusterLabels = $state.raw<Label[] | null>(null);

  let labelRun = 0;

  $effect.pre(() => {
    // Read deps so the effect re-runs when any of them change.
    let col = categoryColumn;
    let xCol = spec.data.x;
    let yCol = spec.data.y;
    // a stale run must not overwrite a newer one's labels
    let run = ++labelRun;
    // gene columns are added after load and never appear here, so this also
    // skips the GROUP BY over every distinct expression value
    let isCategorical = context.columns.some((c) => c.name === col && c.jsType === "string");
    if (!showLabels || col == null || !isCategorical) {
      clusterLabels = null;
      return;
    }
    context.coordinator
      .query(
        SQL.Query.from(context.table)
          .select({
            c: SQL.column(col),
            mx: SQL.sql`MEDIAN(${SQL.column(xCol)})::FLOAT`,
            my: SQL.sql`MEDIAN(${SQL.column(yCol)})::FLOAT`,
            n: SQL.sql`COUNT(*)::INT`,
          })
          .groupby(SQL.column(col)),
      )
      .then((result: any) => {
        if (run !== labelRun) return;
        let labels: Label[] = [];
        for (let row of result) {
          if (row.c == null || row.mx == null || row.my == null) continue;
          labels.push({ x: row.mx, y: row.my, text: String(row.c), level: 0, priority: row.n });
        }
        clusterLabels = labels.length > 0 ? labels : null;
      })
      .catch(() => {
        if (run !== labelRun) return;
        clusterLabels = null;
      });
  });

  // Update the category mapping and legend.
  $effect.pre(() => {
    let promise = context.cache.value(`embedding/category/${categoryColumn}`, () =>
      makeCategoryColumn(context.coordinator, context.table, categoryColumn, theme),
    );
    promise.then((v) => {
      categoryLegend = v;
      if ((categoryLegend?.legend.length ?? 0) > maxCategories) {
        onSpecChange({ mode: "points" });
      }
    });
  });

  $effect.pre(() => {
    let isOnMount = true;
    let previousValue: RowID[] | null = null;
    return highlightStore.subscribe((v) => {
      selection = v;

      // Don't animate immediately on mount.
      if (isOnMount) {
        isOnMount = false;
        previousValue = v;
        return;
      }
      // Animate when a single new point is added.
      let newIDs = v ?? [];
      let oldIDs = previousValue ?? [];
      let enteringIDs = newIDs.filter((x) => oldIDs.indexOf(x) < 0);
      if (enteringIDs.length == 1) {
        animateToPoint(enteringIDs[0]);
      }
      if (tooltip != null && newIDs.indexOf(tooltip) < 0) {
        tooltip = null;
      }
      previousValue = v;
    });
  });

  $effect.pre(() =>
    searchResult.subscribe(async (result) => {
      if (result == null || result.ids.length == 0) {
        overlayProps = null;
        return;
      }
      let centerId: RowID | null = null;
      if (result.mode == "neighbors") {
        centerId = result.query;
      }
      let r = Array.from(
        await context.coordinator.query(
          SQL.Query.from(context.table)
            .select({ identifier: SQL.column(context.id), x: SQL.column(spec.data.x), y: SQL.column(spec.data.y) })
            .where(
              SQL.isIn(
                context.id,
                result.ids.concat(centerId != null ? [centerId] : []).map((x) => SQL.literal(x)),
              ),
            ),
        ),
      ) as DataPoint[];
      overlayProps = {
        center: r.filter((p) => p.identifier === centerId)[0] ?? null,
        points: r.filter((p) => p.identifier !== centerId),
      };
    }),
  );

  async function animateToPoint(identifier: RowID): Promise<void> {
    let defaultScale = await context.cache.value(`embedding/default-viewport-scale/${spec.data.x},${spec.data.y}`, () =>
      defaultViewportScale(context.coordinator, context.table, spec.data.x, spec.data.y),
    );
    let scale = defaultScale * 2;
    // Query the x, y location.
    let result = await context.coordinator.query(
      SQL.Query.from(context.table)
        .select({
          x: SQL.column(spec.data.x),
          y: SQL.column(spec.data.y),
        })
        .where(SQL.eq(SQL.column(context.id), SQL.literal(identifier))),
    );
    let { x, y } = result.get(0) as { x: number; y: number };
    // Start animation and show tooltip.
    startViewportAnimation({ x: x, y: y, scale: scale });
    tooltip = identifier;
  }

  let currentViewportAnimation: number | null;
  let animatingViewport = $state.raw<ViewportState | null>(null);
  function startViewportAnimation(newState: ViewportState) {
    tooltip = null;
    let start = animatingViewport ?? chartState.viewport;
    if (start == null) {
      onStateChange({ viewport: newState });
      return;
    }
    animatingViewport = start;
    let duration = 800;
    let t0 = new Date().getTime();
    let callback = () => {
      let t = (new Date().getTime() - t0) / duration;
      if (t > 1) {
        t = 1;
      }
      animatingViewport = interpolateViewport(start, newState, cubicOut(t));
      if (t < 1) {
        currentViewportAnimation = requestAnimationFrame(callback);
      } else {
        onStateChange({ viewport: animatingViewport });
      }
    };
    if (currentViewportAnimation) {
      cancelAnimationFrame(currentViewportAnimation);
    }
    currentViewportAnimation = requestAnimationFrame(callback);
  }
</script>

<div class="relative">
  <EmbeddingViewMosaic
    width={width}
    height={height}
    coordinator={context.coordinator}
    table={context.table}
    filter={context.filter}
    rangeSelection={context.filter}
    identifier={context.id}
    x={spec.data.x}
    y={spec.data.y}
    text={spec.data.text}
    category={categoryLegend?.indexColumn}
    rowKey={context.id}
    categoryColors={categoryLegend?.categoryColors ??
      categoryLegend?.legend.map((x) => x.color) ?? [theme.embeddingColor]}
    config={{
      colorScheme: $colorScheme,
      ...context.embeddingViewConfig,
      mode: spec.mode ?? "points",
      ...(spec.minimumDensity != null ? { minimumDensity: spec.minimumDensity } : {}),
      ...(spec.pointSize != null ? { pointSize: spec.pointSize } : {}),
      downsampleMaxPoints: spec.downsampleMaxPoints ?? defaultDownsampleMaxPoints,
    }}
    labels={context.embeddingViewLabels}
    cache={context.persistentCache}
    additionalFields={Object.fromEntries(context.columns.map((c) => [c.name, c.name]))}
    customTooltip={{
      class: CustomTooltip,
      props: {
        darkMode: $colorScheme,
        columnStyles: $columnStyles,
        onNearestNeighborSearch:
          (context.searchModes ?? []).indexOf("neighbors") >= 0 ? (id: any) => context.search?.(id, "neighbors") : null,
      },
    }}
    customOverlay={{
      class: CustomOverlay,
      props: {
        ...(overlayProps ?? { points: [], center: null }),
        labels: showLabels ? (clusterLabels ?? []) : [],
      },
    }}
    viewportState={animatingViewport ?? chartState.viewport}
    onViewportState={(v) => onStateChange({ viewport: v })}
    rangeSelectionValue={chartState.brush}
    onRangeSelection={(v) => onStateChange({ brush: v ?? undefined })}
    tooltip={tooltip}
    onTooltip={(v) => {
      tooltip = v;
    }}
    selection={selection}
    onSelection={(points) => {
      selection = points;
      highlightStore.set(points?.map((p) => p.identifier) ?? null);
    }}
  />
  <div class="absolute top-0 left-0 right-0 flex flex-wrap justify-between items-start pointer-events-none">
    {#if categoryLegend != null}
      <div
        class="flex-none m-2 p-2 rounded-md bg-slate-100/75 dark:bg-slate-800/75 backdrop-blur-sm pointer-events-auto order-3"
      >
        {#if categoryLegend.isContinuous}
          <!-- Continuous gradient bar legend with scale selector -->
          <div class="flex flex-col gap-1.5" style="min-width: 180px;">
            <div
              class="h-3 rounded-sm"
              style:background="linear-gradient(to right, {(categoryLegend.categoryColors ?? [])
                .slice(0, 64)
                .join(', ')})"
            ></div>
            <div class="flex justify-between text-xs text-slate-400">
              <span>{categoryLegend.legend[0]?.label ?? ""}</span>
              <span>{categoryLegend.legend[categoryLegend.legend.length - 1]?.label ?? ""}</span>
            </div>
            <select
              class="text-xs bg-transparent border border-slate-300 dark:border-slate-600 rounded px-1 py-0.5 text-slate-400 cursor-pointer"
              value={selectedColorScale}
              onchange={(e) => {
                let newScale = (e.target as HTMLSelectElement).value;
                selectedColorScale = newScale;
                if (categoryLegend?.isContinuous) {
                  categoryLegend = recolorContinuousLegend(categoryLegend, newScale);
                }
              }}
            >
              <optgroup label="Sequential">
                {#each CONTINUOUS_SCALES.filter((s) => s.category === "sequential") as scale}
                  <option value={scale.name}>{scale.label}</option>
                {/each}
              </optgroup>
              <optgroup label="Diverging">
                {#each CONTINUOUS_SCALES.filter((s) => s.category === "diverging") as scale}
                  <option value={scale.name}>{scale.label}</option>
                {/each}
              </optgroup>
            </select>
          </div>
        {:else}
          <Legend
            context={context}
            spec={{ items: categoryLegend.legend }}
            state={chartState.legend ?? {}}
            mode="view"
            onSpecChange={() => {}}
            onStateChange={(update, mode) => {
              onStateChange({ legend: update });
            }}
          />
        {/if}
      </div>
    {/if}
    <div
      class="flex-none p-2 rounded-ss-md rounded-ee-md bg-white/75 dark:bg-black/75 backdrop-blur-sm flex items-center gap-2 pointer-events-auto order-1"
    >
      {#if Object.keys(availableReductions).length > 1}
        <Select
          class="max-w-40"
          label="Reduction"
          value={currentReduction}
          onChange={(v) => switchReduction(v)}
          options={Object.keys(availableReductions).map((name) => ({ value: name, label: name.toUpperCase() }))}
        />
      {/if}
      <SearchableSelect
        class="max-w-64"
        label="Color"
        value={categoryColumn}
        onChange={(v) => setColor(v)}
        placeholder="Search columns…"
        options={[
          { value: undefined, label: "--" },
          ...context.columns
            .filter((c) => c.jsType == "string" || c.jsType == "number" || c.jsType == "Date")
            .map((c) => ({ value: c.name, label: c.name })),
        ]}
      />
      <PopupButton icon={IconSettings} title="Options">
        <div class="flex flex-col gap-2 w-64">
          <label class="flex items-center gap-2 cursor-pointer select-none">
            <input type="checkbox" bind:checked={showLabels} />
            <span class="text-slate-600 dark:text-slate-300">Cluster labels</span>
          </label>
          {#if showLabels && categoryColumn == null}
            <div class="text-xs text-slate-400">Pick a categorical Color column to label.</div>
          {:else if showLabels && (categoryLegend?.isContinuous ?? false)}
            <div class="text-xs text-slate-400">Labels apply to categorical columns, not continuous values.</div>
          {/if}
          <div class="text-slate-500 dark:text-slate-400 select-none">Display Mode</div>
          <div class="flex gap-2 items-center">
            <Select
              value={spec.mode ?? "points"}
              onChange={(v) => onSpecChange({ mode: v })}
              disabled={categoryLegend != null && categoryLegend.legend.length > maxCategories}
              options={[
                { value: "points", label: "Points" },
                { value: "density", label: "Density" },
              ]}
            />
            {#if (spec.mode ?? "points") == "density"}
              <Slider
                bind:value={
                  () => Math.log((spec.minimumDensity ?? defaultMinimumDensity) / defaultMinimumDensity),
                  (v) => onSpecChange({ minimumDensity: defaultMinimumDensity * Math.exp(v) })
                }
                min={-4}
                max={4}
                step={0.05}
              />
            {/if}
          </div>
          <div class="text-slate-500 dark:text-slate-400 select-none">Point Size</div>
          <div class="flex gap-2 items-center">
            <Slider
              bind:value={() => spec.pointSize ?? 1, (v) => onSpecChange({ pointSize: v })}
              min={1}
              max={10}
              step={0.05}
            />
            <Button label="Auto" onClick={() => onSpecChange({ pointSize: undefined })} />
          </div>
          {#if totalPointCount != null && totalPointCount > minDownsampleMaxPoints}
            {@const effectiveLimit = spec.downsampleMaxPoints ?? Math.min(defaultDownsampleMaxPoints, totalPointCount)}
            {@const isMaxed = effectiveLimit >= totalPointCount}
            <div class="text-slate-500 dark:text-slate-400 select-none">
              Max Points: {isMaxed
                ? "All"
                : effectiveLimit >= 1000000
                  ? (effectiveLimit / 1000000).toFixed(1) + "M"
                  : (effectiveLimit / 1000).toFixed(0) + "K"}
              {#if !isMaxed}
                <span class="text-slate-400 dark:text-slate-500"
                  >/ {totalPointCount >= 1000000
                    ? (totalPointCount / 1000000).toFixed(1) + "M"
                    : (totalPointCount / 1000).toFixed(0) + "K"}</span
                >
              {/if}
            </div>
            <div class="flex gap-2 items-center">
              <Slider
                bind:value={
                  () =>
                    spec.downsampleMaxPoints ??
                    Math.min(defaultDownsampleMaxPoints, totalPointCount ?? defaultDownsampleMaxPoints),
                  (v) => onSpecChange({ downsampleMaxPoints: v })
                }
                min={minDownsampleMaxPoints}
                max={totalPointCount}
                step={Math.max(10000, Math.floor(totalPointCount / 100 / 10000) * 10000)}
              />
            </div>
          {/if}
        </div>
      </PopupButton>
    </div>
  </div>
  <!-- Gene search panel (left side, below toolbar) -->
  <div class="absolute top-12 left-2 z-10 pointer-events-auto" style="width: 232px;">
    <div class="p-2 rounded-md bg-slate-100/85 dark:bg-slate-800/85 backdrop-blur-sm">
      <div class="flex gap-3 mb-1.5 text-xs font-medium">
        {#each [{ id: "genes", label: "Genes" }, { id: "markers", label: "Markers" }] as tab}
          <button
            class="pb-0.5 border-b-2 transition-colors {genePanelTab === tab.id
              ? 'border-blue-500 text-slate-700 dark:text-slate-100'
              : 'border-transparent text-slate-500 dark:text-slate-400'}"
            onclick={() => (genePanelTab = tab.id)}
          >
            {tab.label}
          </button>
        {/each}
      </div>
      <div class:hidden={genePanelTab !== "genes"}>
        <GeneSearch coordinator={context.coordinator} onGeneSelect={handleGeneSelect} />
      </div>
      {#if genePanelTab === "markers"}
        <MarkerPanel
          activeColumn={categoryColumn}
          onGeneSelect={handleGeneSelect}
          onResetColor={(column) => setColor(column)}
        />
      {/if}
    </div>
  </div>
</div>
