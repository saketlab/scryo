<!-- scryo: per-cell-type expression violins, computed via two DuckDB queries + hand-drawn SVG -->
<script lang="ts">
  import type { Coordinator } from "@uwdata/mosaic-core";
  import { resolveChartTheme } from "../common/theme.js";
  import { escapeXml, svgStringToPngBlob, downloadBlob } from "./figure_export.js";

  interface Props {
    coordinator: Coordinator;
    table: string;
    // a gene column (e.g. "ALB_RNA") to plot, or null to hide
    gene: string | null;
    annotationColumn: string;
    splitColumn: string | null;
    // the same capped top-N values the UMAP split uses
    splitValues: string[] | null;
    colorScheme: "light" | "dark";
    assay: string;
  }

  let { coordinator, table, gene, annotationColumn, splitColumn, splitValues, colorScheme, assay }: Props = $props();

  const K = 24; // density bins
  const SAMPLE_N = 40; // jittered dots per violin; drawing every cell would be millions of circles
  const SAMPLE_POOL = 40000; // reservoir cap before the per-group random pick, bounds the sort
  const PLOT_H = 168;
  const SLOT_W = 58;
  const MARGIN = { top: 8, bottom: 78, left: 46, right: 8 };

  let theme = $derived(resolveChartTheme(colorScheme));

  let geneLabel = $derived(gene && gene.endsWith(`_${assay}`) ? gene.slice(0, -(assay.length + 1)) : gene);

  interface Violin {
    ct: string;
    grp: string;
    path: string; // SVG points for the mirrored density
    median: { x0: number; x1: number; y: number };
    dotsPath: string; // all jittered dots as one <path> (one node, not N circles)
  }
  interface Model {
    violins: Violin[];
    cats: string[];
    groups: string[];
    lo: number;
    hi: number;
    width: number;
  }

  let colorByCellType = $state(true);
  let sortAsc = $state(false);
  let ctPalette = $state<Record<string, string>>({});
  let rawDens = $state.raw<any[] | null>(null);
  let rawSamp = $state.raw<any[] | null>(null);
  let error = $state<string | null>(null);

  // cell-type colours from the deployed palette (same one the UMAP uses)
  fetch("/data/scryo/colors")
    .then((r) => r.json())
    .then((d) => (ctPalette = d[annotationColumn] ?? {}))
    .catch(() => {});

  // sorting reshapes cached results; no re-query
  let model = $derived(rawDens == null ? null : buildModel(rawDens, rawSamp ?? [], sortAsc));

  function q(id: string): string {
    return '"' + id.replace(/"/g, '""') + '"';
  }

  $effect(() => {
    let g = gene;
    let anno = annotationColumn;
    let split = splitColumn;
    error = null;
    rawDens = null;
    if (g == null || anno == "") return;
    // wait for the capped top-N values
    if (split != null && splitValues == null) return;

    let grpExpr = split ? q(split) : "'all'";
    let inList = split && splitValues ? splitValues.map((v) => `'${v.replace(/'/g, "''")}'`).join(", ") : null;
    let splitFilter = split && inList ? `AND ${q(split)} IN (${inList})` : "";
    let sBlock = `
      WITH s AS (
        SELECT ${q(anno)} AS ct, ${grpExpr} AS grp, ${q(g)}::DOUBLE AS v
        FROM ${q(table)}
        WHERE ${q(g)} IS NOT NULL AND ${q(anno)} IS NOT NULL ${splitFilter}
      )`;
    // density bins + global lo/hi; the median comes from the bins client-side,
    // avoiding a quantile_cont pass over all rows
    let densitySql = `${sBlock},
      b AS (SELECT min(v) AS lo, max(v) AS hi FROM s)
      SELECT s.ct AS ct, CAST(s.grp AS VARCHAR) AS grp,
             least(${K}, greatest(1, CAST(floor((s.v - b.lo) / nullif(b.hi - b.lo, 0) * ${K}) AS INT) + 1)) AS bkt,
             count(*)::INT AS n,
             any_value(b.lo) AS lo, any_value(b.hi) AS hi
      FROM s, b GROUP BY 1, 2, 3`;
    // reservoir-sample first so the per-group random sort runs over SAMPLE_POOL
    // rows instead of the whole table
    let sampleSql = `${sBlock}
      SELECT ct, CAST(grp AS VARCHAR) AS grp, v FROM (
        SELECT ct, grp, v, row_number() OVER (PARTITION BY ct, grp ORDER BY random()) AS rn
        FROM (SELECT * FROM s USING SAMPLE reservoir(${SAMPLE_POOL} ROWS))
      ) WHERE rn <= ${SAMPLE_N}`;

    let cancelled = false;
    Promise.all([coordinator.query(densitySql), coordinator.query(sampleSql)])
      .then(([dens, samp]: any[]) => {
        if (cancelled) return;
        rawSamp = Array.from(samp) as any[];
        rawDens = Array.from(dens) as any[];
      })
      .catch((e: any) => {
        if (!cancelled) error = "could not compute expression (is the gene loaded?)";
        console.error("[scryo] violin query failed", e);
      });

    return () => {
      cancelled = true;
    };
  });

  // first bin whose cumulative count reaches half the total
  function medianBin(bins: Float64Array, total: number): number {
    for (let bin = 1, acc = 0; bin <= K; bin++) {
      acc += bins[bin];
      if (acc >= total / 2) return bin;
    }
    return K;
  }

  function buildModel(rows: any[], sample: any[], ascending: boolean): Model | null {
    if (rows.length == 0) return null;
    let lo = Number(rows[0].lo);
    let hi = Number(rows[0].hi);
    if (!(hi > lo)) hi = lo + 1;

    // counts[ct][grp][bin 1..K]; Map preserves insertion order
    let counts = new Map<string, Map<string, Float64Array>>();
    let groupSet = new Set<string>();
    for (let r of rows) {
      let ct = String(r.ct);
      let grp = String(r.grp);
      groupSet.add(grp);
      if (!counts.has(ct)) counts.set(ct, new Map());
      let byGrp = counts.get(ct)!;
      if (!byGrp.has(grp)) byGrp.set(grp, new Float64Array(K + 1));
      byGrp.get(grp)![Number(r.bkt)] += Number(r.n);
    }
    let samples = new Map<string, Map<string, number[]>>();
    for (let r of sample) {
      let ct = String(r.ct);
      let grp = String(r.grp);
      if (!samples.has(ct)) samples.set(ct, new Map());
      let byGrp = samples.get(ct)!;
      let arr = byGrp.get(grp);
      if (!arr) byGrp.set(grp, (arr = []));
      arr.push(Number(r.v));
    }

    // per cell type: pooled total (default busiest-first order) and pooled median bin (ascending sort)
    let totals = new Map<string, number>();
    let medBinOf = new Map<string, number>();
    for (let [ct, byGrp] of counts) {
      let pooled = new Float64Array(K + 1);
      let s = 0;
      for (let arr of byGrp.values())
        for (let bin = 1; bin <= K; bin++) {
          pooled[bin] += arr[bin];
          s += arr[bin];
        }
      totals.set(ct, s);
      medBinOf.set(ct, medianBin(pooled, s));
    }
    let cats = [...counts.keys()].sort((a, b) =>
      ascending ? medBinOf.get(a)! - medBinOf.get(b)! : totals.get(b)! - totals.get(a)!,
    );
    let groups = [...groupSet].sort();

    let plotW = MARGIN.left + MARGIN.right + cats.length * SLOT_W;
    let yForBin = (bin: number) => MARGIN.top + PLOT_H - ((bin - 0.5) / K) * PLOT_H;
    let yForVal = (v: number) => MARGIN.top + PLOT_H - ((v - lo) / (hi - lo)) * PLOT_H;

    let violins: Violin[] = [];
    for (let ci = 0; ci < cats.length; ci++) {
      let ct = cats[ci];
      let slotCenter = MARGIN.left + ci * SLOT_W + SLOT_W / 2;
      let subW = SLOT_W / groups.length;
      for (let gi = 0; gi < groups.length; gi++) {
        let grp = groups[gi];
        let arr = counts.get(ct)?.get(grp);
        if (arr == null) continue;
        let max = 0;
        for (let v of arr) if (v > max) max = v;
        if (max <= 0) continue;
        let cx = slotCenter - SLOT_W / 2 + subW * (gi + 0.5);
        let halfMax = subW * 0.46;
        // right edge top→bottom, then left edge bottom→top (width-scaled per violin)
        let right: string[] = [];
        let left: string[] = [];
        for (let bin = K; bin >= 1; bin--) {
          let w = (arr[bin] / max) * halfMax;
          right.push(`${(cx + w).toFixed(1)},${yForBin(bin).toFixed(1)}`);
        }
        for (let bin = 1; bin <= K; bin++) {
          let w = (arr[bin] / max) * halfMax;
          left.push(`${(cx - w).toFixed(1)},${yForBin(bin).toFixed(1)}`);
        }
        let dotsPath = "";
        for (let v of samples.get(ct)?.get(grp) ?? []) {
          let frac = (v - lo) / (hi - lo);
          let bin = Math.min(K, Math.max(1, Math.floor(frac * K) + 1));
          let localHalf = (arr[bin] / max) * halfMax;
          let dx = (cx + (Math.random() * 2 - 1) * localHalf * 0.85).toFixed(1);
          let dy = yForVal(v).toFixed(1);
          dotsPath += `M${dx},${dy}m-0.9,0a0.9,0.9 0 1,0 1.8,0a0.9,0.9 0 1,0 -1.8,0`;
        }
        let grpTotal = 0;
        for (let bin = 1; bin <= K; bin++) grpTotal += arr[bin];
        let medBin = medianBin(arr, grpTotal);
        violins.push({
          ct,
          grp,
          path: right.concat(left).join(" "),
          median: { x0: cx - halfMax, x1: cx + halfMax, y: yForBin(medBin) },
          dotsPath,
        });
      }
    }
    return { violins, cats, groups, lo, hi, width: plotW };
  }

  let groupColors = $derived.by(() => {
    let m = new Map<string, string>();
    if (model == null || model.groups.length <= 1) return m;
    let palette = theme.categoryColors;
    let colors = typeof palette == "function" ? palette(model.groups.length) : palette;
    model.groups.forEach((g, i) => m.set(g, colors[i % colors.length]));
    return m;
  });
  // deployed cell-type palette, else a categorical fallback indexed by a stable (alphabetical) order
  let cellTypeColors = $derived.by(() => {
    let m = new Map<string, string>();
    if (model == null) return m;
    let palette = theme.categoryColors;
    let colors = typeof palette == "function" ? palette(model.cats.length) : palette;
    [...model.cats].sort().forEach((ct, i) => m.set(ct, ctPalette[ct] ?? colors[i % colors.length]));
    return m;
  });
  let isSplit = $derived((model?.groups.length ?? 1) > 1);
  function colorFor(ct: string, grp: string): string {
    if (isSplit) return groupColors.get(grp) ?? theme.markColor;
    if (colorByCellType) return cellTypeColors.get(ct) ?? theme.markColor;
    return theme.markColor;
  }

  let svgH = MARGIN.top + PLOT_H + MARGIN.bottom;

  // export SVG: black on white, bottom margin scaled to the longest rotated label
  function violinExportSvg(): string {
    if (model == null) return "";
    let BLACK = "#000";
    let split = isSplit;
    let maxLabel = Math.max(1, ...model.cats.map((c) => c.length));
    let bottom = Math.round(Math.min(240, 30 + maxLabel * 3.7)); // room for -40° rotated labels
    let legendW = split ? 24 + Math.max(...model.groups.map((g) => g.length)) * 6.2 : 0;
    let x0 = MARGIN.left;
    let yTop = MARGIN.top;
    let yBot = MARGIN.top + PLOT_H;
    let W = Math.round(model.width + legendW + 8);
    let H = yBot + bottom;
    let p: string[] = [];
    p.push(
      `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="Arial, Helvetica, sans-serif">`,
    );
    p.push(`<rect width="${W}" height="${H}" fill="#ffffff"/>`);
    // y axis + 6 ticks
    p.push(`<line x1="${x0}" x2="${x0}" y1="${yTop}" y2="${yBot}" stroke="${BLACK}"/>`);
    for (let t = 0; t < 6; t++) {
      let frac = t / 5;
      let yy = yBot - frac * PLOT_H;
      let val = model.lo + frac * (model.hi - model.lo);
      p.push(`<line x1="${x0 - 4}" x2="${x0}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}" stroke="${BLACK}"/>`);
      p.push(`<text x="${x0 - 7}" y="${(yy + 3).toFixed(1)}" text-anchor="end" font-size="11" fill="${BLACK}">${val.toFixed(1)}</text>`);
    }
    let midY = yTop + PLOT_H / 2;
    p.push(
      `<text x="13" y="${midY}" text-anchor="middle" font-size="12" fill="${BLACK}" transform="rotate(-90, 13, ${midY})">${escapeXml(geneLabel ?? "")} expression</text>`,
    );
    for (let v of model.violins) {
      let col = colorFor(v.ct, v.grp);
      p.push(`<polygon points="${v.path}" fill="${col}" fill-opacity="${split ? 0.6 : 0.7}" stroke="${col}" stroke-width="0.5"/>`);
      p.push(`<path d="${v.dotsPath}" fill="${BLACK}" fill-opacity="0.45"/>`);
      p.push(`<line x1="${v.median.x0}" x2="${v.median.x1}" y1="${v.median.y}" y2="${v.median.y}" stroke="${BLACK}" stroke-width="1.5"/>`);
    }
    model.cats.forEach((ct, i) => {
      let x = MARGIN.left + i * SLOT_W + SLOT_W / 2;
      p.push(
        `<text x="${x}" y="${yBot + 12}" text-anchor="end" font-size="11" fill="${BLACK}" transform="rotate(-40, ${x}, ${yBot + 12})">${escapeXml(ct)}</text>`,
      );
    });
    if (split) {
      let lx = model.width + 8;
      p.push(`<text x="${lx}" y="${yTop + 6}" font-size="11" font-weight="bold" fill="${BLACK}">${escapeXml(splitColumn ?? "")}</text>`);
      model.groups.forEach((g, i) => {
        let yy = yTop + 22 + i * 17;
        p.push(`<rect x="${lx}" y="${yy - 9}" width="11" height="11" fill="${colorFor("", g)}"/>`);
        p.push(`<text x="${lx + 16}" y="${yy}" font-size="11" fill="${BLACK}">${escapeXml(g)}</text>`);
      });
    }
    p.push("</svg>");
    return p.join("");
  }

  async function exportViolin(format: "png" | "svg") {
    let svg = violinExportSvg();
    if (svg === "") return;
    let name = `violin-${geneLabel}`;
    if (format === "svg") {
      downloadBlob(svg, `${name}.svg`, "image/svg+xml");
    } else {
      downloadBlob(await svgStringToPngBlob(svg, 3), `${name}.png`, "image/png");
    }
  }
</script>

{#if gene != null}
  <div class="rounded-md bg-white/90 dark:bg-slate-800/90 backdrop-blur-sm p-2 shadow-sm">
    <div class="flex items-baseline gap-2 mb-1 px-1 flex-wrap">
      <span class="text-xs font-medium text-slate-700 dark:text-slate-200">{geneLabel}</span>
      <span class="text-[11px] text-slate-400">
        expression across {annotationColumn}{splitColumn ? ` · split by ${splitColumn}` : ""}
      </span>
      <span class="flex items-center gap-1.5 ml-auto">
        {#if !isSplit}
          <button
            class="px-1.5 py-0.5 text-[11px] rounded border transition-colors {colorByCellType
              ? 'border-blue-500 text-blue-600 dark:text-blue-400'
              : 'border-slate-300 dark:border-slate-600 text-slate-500 dark:text-slate-400'}"
            onclick={() => (colorByCellType = !colorByCellType)}
            title="Colour each violin by its cell type"
          >
            cell-type colour
          </button>
        {/if}
        <button
          class="px-1.5 py-0.5 text-[11px] rounded border transition-colors {sortAsc
            ? 'border-blue-500 text-blue-600 dark:text-blue-400'
            : 'border-slate-300 dark:border-slate-600 text-slate-500 dark:text-slate-400'}"
          onclick={() => (sortAsc = !sortAsc)}
          title="Sort cell types by ascending median expression"
        >
          sort ↑
        </button>
        <button
          class="px-1.5 py-0.5 text-[11px] rounded border border-slate-300 dark:border-slate-600 text-slate-500 dark:text-slate-400 hover:border-blue-400"
          onclick={() => exportViolin("png")}
          title="Download violin as PNG"
        >
          PNG
        </button>
        <button
          class="px-1.5 py-0.5 text-[11px] rounded border border-slate-300 dark:border-slate-600 text-slate-500 dark:text-slate-400 hover:border-blue-400"
          onclick={() => exportViolin("svg")}
          title="Download violin as SVG"
        >
          SVG
        </button>
      </span>
      {#if model != null && model.groups.length > 1}
        <span class="flex items-center gap-2 flex-wrap">
          {#each model.groups as grp}
            <span class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-300">
              <span class="inline-block w-2.5 h-2.5 rounded-sm" style:background={colorFor("", grp)}></span>{grp}
            </span>
          {/each}
        </span>
      {/if}
    </div>
    {#if error}
      <div class="text-[11px] text-slate-400 px-1 py-6">{error}</div>
    {:else if model == null}
      <div class="text-[11px] text-slate-400 px-1 py-6">computing…</div>
    {:else}
      <div class="overflow-x-auto">
        <svg width={model.width} height={svgH} style="max-width: none;">
          <line
            x1={MARGIN.left}
            x2={MARGIN.left}
            y1={MARGIN.top}
            y2={MARGIN.top + PLOT_H}
            stroke={theme.gridColor}
            stroke-width="1"
          />
          <text x={MARGIN.left - 6} y={MARGIN.top + 4} text-anchor="end" font-size="9" fill={theme.labelColor}>
            {model.hi.toFixed(1)}
          </text>
          <text x={MARGIN.left - 6} y={MARGIN.top + PLOT_H} text-anchor="end" font-size="9" fill={theme.labelColor}>
            {model.lo.toFixed(1)}
          </text>
          <text
            x={12}
            y={MARGIN.top + PLOT_H / 2}
            text-anchor="middle"
            font-size="10"
            fill={theme.labelColor}
            transform="rotate(-90, 12, {MARGIN.top + PLOT_H / 2})"
          >
            {geneLabel} expr
          </text>
          {#each model.violins as v}
            <polygon
              points={v.path}
              fill={colorFor(v.ct, v.grp)}
              fill-opacity={model.groups.length > 1 ? 0.5 : 0.6}
              stroke={colorFor(v.ct, v.grp)}
              stroke-opacity="0.5"
              stroke-width="0.5"
            />
            <path d={v.dotsPath} fill={theme.labelColor} fill-opacity="0.4" />
            <line
              x1={v.median.x0}
              x2={v.median.x1}
              y1={v.median.y}
              y2={v.median.y}
              stroke={theme.labelColor}
              stroke-width="1.5"
            />
          {/each}
          {#each model.cats as ct, i}
            {@const x = MARGIN.left + i * SLOT_W + SLOT_W / 2}
            <text
              {x}
              y={MARGIN.top + PLOT_H + 12}
              text-anchor="end"
              font-size="9"
              fill={theme.labelColor}
              transform="rotate(-40, {x}, {MARGIN.top + PLOT_H + 12})"
            >
              {ct}
            </text>
          {/each}
        </svg>
      </div>
    {/if}
  </div>
{/if}
